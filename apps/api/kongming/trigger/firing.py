"""触发执行核心:目标派发、run 落库与重试链推进、出站通知、失败落库。

由管理面(create/update)、调度(cron/重试)与入站(webhook/email)共用;
执行永不逃逸——任何异常收敛为 FAILED run。"""

import datetime as dt
import hashlib
import hmac
import json
import logging
import uuid as uuid_mod
from typing import Any, Protocol, cast

from kongming.trigger.ports import (
    EventJudge,
    InputGuardrail,
    MailClient,
    Notifier,
    SecretCipher,
    TargetChecker,
    TargetRunner,
    TaskGateway,
    TriggerRepo,
)
from kongming.trigger.retry import DEFAULT_BACKOFF_SEC, schedule_retry
from kongming.trigger.task_target import (
    event_material,
    filter_pass,
    goal_preview,
    thread_key,
    thread_summary_json,
)
from kongming.trigger.validation import TASK_TARGET, now

# 服务日志名保持 kongming.trigger.service(拆分模块后外部仍按此名寻址服务日志)
log = logging.getLogger("kongming.trigger.service")


class TriggerOperLogger(Protocol):
    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


class FiringCore:
    """执行与落库基体:各用例服务组合后暴露同一套触发/重试/落库能力。

    门面用同一套关键字装配全部用例簇,因此这里同时接收各簇专用端口的
    关键字(target_checker/mail/input_guard/event_judge/task_gateway)并
    落为实例属性;未装配的簇只按自身需要读取。"""

    def __init__(
        self,
        *,
        repo: TriggerRepo,
        cipher: SecretCipher,
        runner: TargetRunner | None,
        notifier: Notifier | None = None,
        oper: TriggerOperLogger | None = None,
        retry_backoff_sec: int = DEFAULT_BACKOFF_SEC,
        target_checker: TargetChecker | None = None,
        mail: MailClient | None = None,
        input_guard: InputGuardrail | None = None,
        event_judge: EventJudge | None = None,
        task_gateway: TaskGateway | None = None,
    ) -> None:
        self._repo = repo
        self._cipher = cipher
        self._runner = runner
        self._notifier = notifier
        self._oper = oper
        # 部署级固定退避(重试次数上限在触发器行,间隔属部署)
        self._retry_backoff_sec = retry_backoff_sec
        self._target_checker = target_checker
        # IMAP 客户端与输入护栏(组合根绑定;未注入=邮件形态不可轮询)
        self._mail = mail
        self._input_guard = input_guard
        # 事件判读与任务侧查/写(组合根绑定;判读未注入=task 目标
        # webhook/email 一律按判读不可用 SKIPPED,fail-closed)
        self._event_judge = event_judge
        self._task_gateway = task_gateway

    async def _audit(self, action: str, target: str, detail: dict[str, object]) -> None:
        if self._oper is not None:
            await self._oper.log(action=action, target=target, detail=detail)

    async def record_failed(self, *, tenant_id: str, trigger_id: str, reason: str) -> None:
        """调度器止损落 FAILED(cron 重算失败等)。"""
        await self._repo.insert_run(
            tenant_id=tenant_id,
            trigger_id=trigger_id,
            status="FAILED",
            error=reason,
            duration_ms=0,
            target_run_ref="",
        )
        await self._repo.commit()

    async def record_skipped(self, *, tenant_id: str, trigger_id: str, reason: str) -> None:
        """跳过落 SKIPPED(并发保护/最小间隔等;不触发重试不通知)。"""
        await self._repo.insert_run(
            tenant_id=tenant_id,
            trigger_id=trigger_id,
            status="SKIPPED",
            error=reason,
            duration_ms=0,
            target_run_ref="",
        )
        await self._repo.commit()

    async def _record_skipped(self, *, row: dict[str, Any], reason: str) -> None:
        """闸门跳过落库(SKIPPED 不触发重试不通知,与并发保护同语义)。"""
        await self._repo.insert_run(
            tenant_id=str(row["tenant_id"]),
            trigger_id=str(row["id"]),
            status="SKIPPED",
            error=reason,
            duration_ms=0,
            target_run_ref="",
            summary="",
        )
        await self._repo.commit()

    async def mark_fired(
        self, *, trigger_id: str, last_fired_iso: str, next_fire_iso: str | None
    ) -> None:
        """推进 last_fired/next_fire 并提交(调度节流共用)。"""
        await self._repo.mark_fired(
            trigger_id=trigger_id,
            last_fired_iso=last_fired_iso,
            next_fire_iso=next_fire_iso,
        )
        await self._repo.commit()

    async def _fire(
        self,
        row: dict[str, Any],
        *,
        inputs: dict[str, Any],
        channel: str,
        pending_run: dict[str, Any] | None = None,
        summary_json: str = "",
    ) -> None:
        """执行触发器目标、保存 run 并推进重试。只有成功、重试耗尽或未配置
        重试时发送通知;挂起重试不通知。"""
        started = now()
        attempt = int(pending_run["attempt"]) if pending_run is not None else 1
        status, ref, error, summary_json = await self._execute(
            row, inputs=inputs, channel=channel, summary_json=summary_json
        )
        await self._settle(
            row,
            inputs=inputs,
            status=status,
            ref=ref,
            error=error,
            summary_json=summary_json,
            attempt=attempt,
            pending_run=pending_run,
            started=started,
        )

    async def _settle(
        self,
        row: dict[str, Any],
        *,
        inputs: dict[str, Any],
        status: str,
        ref: str,
        error: str,
        summary_json: str,
        attempt: int,
        pending_run: dict[str, Any] | None,
        started: dt.datetime,
    ) -> None:
        """执行后的落定段:时长计量 → 重试判定 → 终态通知 → 落库 → 追加挂起行。"""
        duration = int((now() - started).total_seconds() * 1000)
        # 重试判定(纯函数,单源):FAILED 且剩余次数>0 → 安排;否则链终态
        retry_at = schedule_retry(
            status=status,
            attempt=attempt,
            max_retries=int(row.get("max_retries") or 0),
            now=now(),
            backoff_sec=self._retry_backoff_sec,
        )
        if retry_at is None:
            # 链终态(成功/耗尽/未配重试):出站通知恰一次(max_retries=0 时
            # 即既有行为——执行落定即通知)
            notify_err = await self._notify(row, status=status, error=error, inputs=inputs)
            if notify_err:
                error = (error + ";" if error else "") + f"通知失败: {notify_err}"
        chain_root = await self._land_run(
            row,
            status=status,
            error=error,
            duration=duration,
            ref=ref,
            summary_json=summary_json,
            pending_run=pending_run,
        )
        if chain_root is None:
            return
        if retry_at is not None:
            # 链未终态:追加挂起重试行(独立 run 行,attempt 递增并关联首跑)
            await self._repo.insert_run(
                tenant_id=str(row["tenant_id"]),
                trigger_id=str(row["id"]),
                status="PENDING",
                error="",
                duration_ms=0,
                target_run_ref="",
                attempt=attempt + 1,
                retry_of_run_id=chain_root,
                next_retry_at=retry_at,
            )
        await self._repo.commit()

    async def _execute(
        self, row: dict[str, Any], *, inputs: dict[str, Any], channel: str, summary_json: str
    ) -> tuple[str, str, str, str]:
        """调用目标执行并归类结果;返回 (status, 目标 run 引用, 错误文案, summary)。"""
        try:
            if self._runner is None:
                raise RuntimeError("TargetRunner 未注入(部署缺陷)")
            ok, ref, err = await self._runner.run_target(
                target_type=str(row["target_type"]),
                target_key=str(row["target_key"]),
                tenant_id=str(row["tenant_id"]),
                tenant_key="",  # 由实现侧反解(定义行有 tenant_id)
                creator_id=str(row["created_by"]),
                inputs=inputs,
                channel=channel,
                template=cast("dict[str, Any] | None", row.get("task_template")),
            )
        except Exception as e:  # noqa: BLE001  # 触发执行永不逃逸
            return "FAILED", "", f"{type(e).__name__}: {str(e)[:400]}", summary_json
        if not ok:
            return "FAILED", ref, err, summary_json
        if str(row["target_type"]) != TASK_TARGET:
            return "SUCCESS", ref, "", summary_json
        # task 目标:成功 run 摘要改写为线程键载体(去重判定依据;
        # 目标 run 关联=立项任务 id,后续生命周期归任务域账本)
        tk = thread_key(row, inputs)
        if tk:
            summary_json = thread_summary_json(tk, ref)
        return "SUCCESS", ref, "", summary_json

    async def _land_run(
        self,
        row: dict[str, Any],
        *,
        status: str,
        error: str,
        duration: int,
        ref: str,
        summary_json: str,
        pending_run: dict[str, Any] | None,
    ) -> str | None:
        """落定本次执行行;返回链首 id(链已取消返回 None,调用方不再建重试行)。"""
        if pending_run is None:
            # 普通触发:落链首行(attempt=1);id 留作后续挂起行的链首关联
            return await self._repo.insert_run(
                tenant_id=str(row["tenant_id"]),
                trigger_id=str(row["id"]),
                status=status,
                error=error,
                duration_ms=duration,
                target_run_ref=ref,
                summary=summary_json,
            )
        # 挂起重试在原行落定终态(独立行,历史中间失败全保留);
        # 链首沿用挂起行携带的关联(链内传递,免回查)
        chain_root = str(pending_run["retry_of_run_id"])
        landed = await self._repo.update_run_terminal(
            run_id=str(pending_run["id"]),
            status=status,
            error=error,
            duration_ms=duration,
            target_run_ref=ref,
        )
        if landed != 0:
            return chain_root
        # 派发期间触发器若被停用或删除，仍补记执行结果行(通知状态如实)，但不再创建后续重试。
        await self._repo.insert_run(
            tenant_id=str(row["tenant_id"]),
            trigger_id=str(row["id"]),
            status=status,
            error=f"链已取消,结果补记: {error}" if error else "链已取消,结果补记",
            duration_ms=duration,
            target_run_ref=ref,
            attempt=int(pending_run["attempt"]),
            retry_of_run_id=chain_root,
        )
        await self._repo.commit()
        return None

    async def _notify(
        self, row: dict[str, Any], *, status: str, error: str, inputs: dict[str, Any]
    ) -> str:
        """出站通知(SSRF 判定走 notifier.check_url;HMAC 签名经
        X-Kongming-Signature 头发送;5s 超时);返回错误文案(空=成功/未配置)。"""
        url = str(row.get("notify_url") or "")
        if not url:
            return ""
        if self._notifier is None:
            return "通知器未接线(部署缺陷)"
        try:
            await self._notifier.check_url(url)
        except Exception as e:
            return f"SSRF 拒绝: {e}"
        body = json.dumps(
            {
                "trigger": row["tg_key"],
                "status": status,
                "error": error,
                "finished_at": now().isoformat(),
            },
            ensure_ascii=False,
        ).encode()
        signature, sign_error = self._sign_notify(row, body)
        if signature is None:
            return sign_error  # 签名失败文案(解密失败,见下)
        try:
            await self._notifier.post(url=url, body=body, signature=signature)
        except Exception as e:  # noqa: BLE001  # 通知失败不掩执行结果
            return f"{type(e).__name__}: {str(e)[:200]}"
        return ""

    def _sign_notify(self, row: dict[str, Any], body: bytes) -> tuple[str | None, str]:
        """返回 (签名, 错误文案);解密失败时签名为 None 并给出错误文案。"""
        secret = ""
        if row.get("secret_cipher"):
            # 解密失败(密钥轮换后旧密文/密文损坏)必须收敛为通知错误文案:
            # 此处抛出会逃出 _fire 的通知段之外→ insert_run 被跳过+next_fire
            # 不推进 → 调度器每 30s 无限重触发且零 run 零审计(与入站侧
            # 密钥轮换场景对称设防)
            try:
                secret = self._cipher.decrypt(str(row["secret_cipher"]))
            except Exception as e:  # noqa: BLE001
                return None, f"签名密钥解密失败(须轮换重存 secret): {type(e).__name__}"
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), ""

    async def _gate_task_target(
        self, *, row: dict[str, Any], inputs: dict[str, Any]
    ) -> tuple[bool, str]:
        """task 目标过滤闸(WEBHOOK/EMAIL 消费;CRON 不经闸):程序层确定性
        谓词 → 语义层单次判读 → 线程去重。返回 (通过, 跳过原因)。"""
        tpl = cast("dict[str, Any]", row.get("task_template") or {})
        if not filter_pass(cast("dict[str, Any] | None", tpl.get("filter")), inputs):
            return False, "程序层过滤:事件不匹配触发器条件"
        ok, reason = await self._judge_event(row, tpl, inputs)
        if not ok:
            return False, reason
        return await self._dedupe_thread(row, inputs)

    async def _judge_event(
        self, row: dict[str, Any], tpl: dict[str, Any], inputs: dict[str, Any]
    ) -> tuple[bool, str]:
        """语义层单次判读:端口缺失/不可达/否决均跳过(原因可审计)。"""
        if self._event_judge is None:
            return False, "判读不可用:判读端口未接线(按不可用口径 SKIPPED)"
        verdict = await self._event_judge.judge(
            source=str(row["adapter"]),
            summary=event_material(inputs),
            goal_preview=goal_preview(tpl),
        )
        if verdict is None:
            return False, "判读不可用(端点不可达/超时/报文畸形)"
        if verdict.verdict != "create":
            return False, f"语义判读否决: {verdict.reason}"
        return True, ""

    async def _dedupe_thread(self, row: dict[str, Any], inputs: dict[str, Any]) -> tuple[bool, str]:
        """线程去重:同线程键存在未终态触发任务 → 事件归属既有任务,不重复立项。"""
        tk = thread_key(row, inputs)
        if not tk:
            return True, ""
        last = await self._repo.latest_success_run_for_thread(
            trigger_id=str(row["id"]), thread_key=tk
        )
        ref = str(last.get("target_run_ref") or "") if last else ""
        if not ref or self._task_gateway is None:
            return True, ""
        if not await self._task_gateway.is_open(task_id=ref):
            return True, ""
        await self._task_gateway.append_thread_event(
            task_id=ref,
            event={
                "at": now(),
                "kind": "trigger_event",
                "thread_key": tk,
                "summary": event_material(inputs),
            },
        )
        return False, f"线程 {tk} 已有在途任务,事件已追加至既有任务"


def replay_inputs(parsed: object) -> dict[str, Any]:
    """重试回放输入:链首 summary 解析为对象才用,否则空输入(不因脏摘要拒绝重试)。"""
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}


def new_webhook_path() -> str:
    """webhook 路径(随机 UUID,不可猜)。"""
    return str(uuid_mod.uuid4())


def generated_secret() -> str:
    """缺省 webhook secret(创建响应一次性展示)。"""
    return "whsec_" + uuid_mod.uuid4().hex


def parse_json_body(body: bytes) -> dict[str, Any]:
    """请求体必须是 JSON 对象(其余抛 ValueError,入站侧同形拒)。"""
    parsed = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError("payload 须为 JSON 对象")
    return cast("dict[str, Any]", parsed)
