"""trigger 用例编排(031):CRUD 校验 + 目标执行 + 出站通知 +
执行历史。零框架(宪法 Article II);时区固定 Asia/Shanghai。
108:邮件入站形态——UID 游标轮询 + 摘要白名单适配 + 输入护栏三层
(轮询器是「形态」,重试是「策略」,正交共存)。"""

import datetime as dt
import hashlib
import hmac as hmac_mod
import json
import logging
import re
import uuid as uuid_mod
from typing import Any, Protocol, cast

from kongming.trigger.adapters import (
    AdapterError,
    normalize,
    normalize_email,
)
from kongming.trigger.models import CronError, next_fire, parse_cron
from kongming.trigger.ports import (
    InputGuardrail,
    MailAuthError,
    MailClient,
    MailConnectError,
    MailError,
    Notifier,
    SecretCipher,
    TargetChecker,
    TargetRunner,
    TriggerRepo,
)
from kongming.trigger.retry import DEFAULT_BACKOFF_SEC, is_valid_max_retries, schedule_retry

TG_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Shanghai(design O5 挂账可配)
_MAX_BODY = 1 * 1024 * 1024

# 108:邮件轮询节流下限封顶(design:轮询风暴防线)与每轮处理数上限
# (剩余下轮续传,游标语义天然支持)
MIN_POLL_INTERVAL_SEC = 30
MAX_MESSAGES_PER_POLL = 20


class TriggerValidationError(Exception):
    """领域层 400(key/cron/目标形态)。"""


class TriggerNotFound(Exception):
    """不存在/跨租户同形。"""


class TriggerKeyTaken(Exception):
    """同租户 tg_key 冲突(409 语义,R1-S2)。"""


class TargetNotFound(Exception):
    """target_key 在当前租户对应目标域不存在(040 写边界闸,400 TARGET_NOT_FOUND)。"""


class WebhookRejected(Exception):
    """签名校验失败/禁用/不存在——同形(防探测)。"""


class TriggerOperLogger(Protocol):
    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


log = logging.getLogger(__name__)


def _now() -> dt.datetime:
    return dt.datetime.now(_TZ)


class TriggerService:
    def __init__(
        self,
        *,
        repo: TriggerRepo,
        cipher: SecretCipher,
        runner: TargetRunner | None,
        notifier: Notifier | None = None,
        oper: TriggerOperLogger | None = None,
        target_checker: TargetChecker | None = None,
        retry_backoff_sec: int = DEFAULT_BACKOFF_SEC,
        mail: MailClient | None = None,
        input_guard: InputGuardrail | None = None,
    ) -> None:
        self._repo = repo
        self._cipher = cipher
        self._runner = runner
        self._notifier = notifier
        self._oper = oper
        self._target_checker = target_checker
        # 101:部署级固定退避(重试次数上限在触发器行,间隔属部署)
        self._retry_backoff_sec = retry_backoff_sec
        # 108:IMAP 客户端与输入护栏(组合根绑定;未注入=邮件形态不可轮询)
        self._mail = mail
        self._input_guard = input_guard

    async def _audit(self, action: str, target: str, detail: dict[str, object]) -> None:
        if self._oper is not None:
            await self._oper.log(action=action, target=target, detail=detail)

    # ── 管理面 ──

    async def create(
        self,
        *,
        tenant_id: str,
        tg_key: str,
        name: str,
        type_: str,
        target_type: str,
        target_key: str,
        cron_expr: str | None,
        min_interval_sec: int,
        adapter: str,
        notify_url: str,
        secret: str,
        created_by: str,
        max_retries: int = 0,
        imap_host: str = "",
        imap_port: int = 993,
        imap_tls: bool = True,
        imap_user: str = "",
        imap_password: str = "",
        imap_folder: str = "INBOX",
        imap_poll_interval_sec: int = 300,
    ) -> dict[str, Any]:
        if not TG_KEY_RE.fullmatch(tg_key):
            raise TriggerValidationError("tg_key 须为小写字母开头的字母/数字/下划线(2-64 位)")
        if not name.strip() or len(name) > 128:
            raise TriggerValidationError("名称非空且 ≤128 字符")
        if type_ not in ("CRON", "WEBHOOK", "EMAIL"):
            raise TriggerValidationError("type 须为 CRON/WEBHOOK/EMAIL")
        if target_type not in ("workflow", "bot"):
            raise TriggerValidationError("target_type 须为 workflow/bot")
        if adapter not in ("custom", "github"):
            raise TriggerValidationError("adapter 须为 custom/github")
        # 101:重试次数上限值域(0–5,0=不重试;与 DDL CHECK 同源判定)
        if not is_valid_max_retries(max_retries):
            raise TriggerValidationError("max_retries 须为 0–5 的整数(0=不重试)")
        if type_ == "CRON":
            if not cron_expr:
                raise TriggerValidationError("CRON 触发器须给 cron_expr")
            try:
                parse_cron(cron_expr)
            except CronError as e:
                raise TriggerValidationError(str(e)) from e
        webhook_path: str | None = None
        plain_secret_out: str | None = None
        if type_ == "WEBHOOK":
            webhook_path = str(uuid_mod.uuid4())
            if not secret:
                secret = "whsec_" + uuid_mod.uuid4().hex  # 缺省生成(一次性展示)
            plain_secret_out = secret
        # 108:邮件形态保存期校验(与 cron 表达式同口径,拼错配置不进调度);
        # 凭证即刻加密,任何响应不回显(创建亦无一次性展示——口令是用户自填的)
        imap_credential_cipher = ""
        next_fire_iso: str | None = None
        if type_ == "EMAIL":
            if not imap_host.strip():
                raise TriggerValidationError("EMAIL 触发器须给 IMAP 主机")
            if not imap_user.strip():
                raise TriggerValidationError("EMAIL 触发器须给 IMAP 账号")
            if not imap_password:
                raise TriggerValidationError("EMAIL 触发器须给 IMAP 凭证(密码)")
            if not 1 <= int(imap_port) <= 65535:  # pydantic 已拦;领域层同源兜底
                raise TriggerValidationError("IMAP 端口须为 1–65535")
            imap_folder = imap_folder.strip() or "INBOX"
            imap_poll_interval_sec = max(MIN_POLL_INTERVAL_SEC, int(imap_poll_interval_sec))
            imap_credential_cipher = self._cipher.encrypt(imap_password)
            next_fire_iso = _now().isoformat()  # 创建即到期,首个 tick 轮询
        if type_ == "CRON" and cron_expr:
            try:
                next_fire_iso = next_fire(cron_expr, _now()).isoformat()
            except CronError as e:
                # R1-S1a:计算失败(永不触发/366 天外)同属保存期 400
                raise TriggerValidationError(str(e)) from e
        # 040 写边界闸:目标不存在即 400,防拼错目标静默挂到运行期才暴露
        if self._target_checker is not None and not await self._target_checker.target_exists(
            target_type=target_type,
            target_key=target_key,
            tenant_id=tenant_id,
            creator_id=created_by,
        ):
            raise TargetNotFound(target_key)
        # R1-S2:重名预检(唯一索引兜底之外的友好 409,与 datasource 同范式)
        if await self._repo.get(tenant_id=tenant_id, tg_key=tg_key) is not None:
            raise TriggerKeyTaken(tg_key)
        tid = await self._repo.create(
            tenant_id=tenant_id,
            tg_key=tg_key,
            name=name.strip(),
            type=type_,
            target_type=target_type,
            target_key=target_key,
            cron_expr=cron_expr,
            min_interval_sec=min_interval_sec,
            webhook_path=webhook_path,
            secret_cipher=self._cipher.encrypt(secret) if secret else "",
            adapter=adapter,
            notify_url=notify_url,
            created_by=created_by,
            next_fire_at=next_fire_iso,
            max_retries=max_retries,
            imap_host=imap_host.strip(),
            imap_port=int(imap_port),
            imap_tls=bool(imap_tls),
            imap_user=imap_user.strip(),
            imap_credential=imap_credential_cipher,
            imap_folder=imap_folder if type_ == "EMAIL" else "INBOX",
            imap_poll_interval_sec=int(imap_poll_interval_sec),
        )
        await self._audit("trigger.create", tg_key, {"type": type_, "target": target_key})
        out: dict[str, Any] = {"id": tid, "webhook_path": webhook_path}
        if plain_secret_out:
            out["secret"] = plain_secret_out  # 创建一次性展示(与 025 api-key 同哲学)
        return out

    async def update(self, *, trigger_id: str, tenant_id: str, **kw: Any) -> None:
        row = await self._repo.get_by_id(trigger_id=trigger_id)
        if row is None or str(row["tenant_id"]) != tenant_id:
            raise TriggerNotFound(trigger_id)
        # R1-T4:密钥轮换(仅显式非空才重加密;空=保持现值)
        new_secret = str(kw.pop("secret", "") or "")
        if new_secret:
            kw["secret_cipher"] = self._cipher.encrypt(new_secret)
        # 101:重试次数上限值域(仅显式提交时校验)
        if "max_retries" in kw and not is_valid_max_retries(int(kw["max_retries"])):
            raise TriggerValidationError("max_retries 须为 0–5 的整数(0=不重试)")
        # 108:邮件配置列仅 EMAIL 形态更新(空框=凭证保持现值;CRON/WEBHOOK
        # 行剥除邮件字段,防误覆盖);配置变更即到期,下个 tick 按新配置轮询
        imap_password = str(kw.pop("imap_password", "") or "")
        if str(row["type"]) == "EMAIL":
            if not str(kw.get("imap_host", "") or "").strip():
                raise TriggerValidationError("EMAIL 触发器须给 IMAP 主机")
            if not str(kw.get("imap_user", "") or "").strip():
                raise TriggerValidationError("EMAIL 触发器须给 IMAP 账号")
            kw["imap_host"] = str(kw["imap_host"]).strip()
            kw["imap_user"] = str(kw["imap_user"]).strip()
            if not 1 <= int(kw.get("imap_port", 993)) <= 65535:
                raise TriggerValidationError("IMAP 端口须为 1–65535")
            kw["imap_folder"] = str(kw.get("imap_folder") or "").strip() or "INBOX"
            kw["imap_poll_interval_sec"] = max(
                MIN_POLL_INTERVAL_SEC, int(kw.get("imap_poll_interval_sec") or 300)
            )
            if imap_password:
                kw["imap_credential"] = self._cipher.encrypt(imap_password)
            kw["next_fire_at"] = _now().isoformat()
        else:
            for f in (
                "imap_host",
                "imap_port",
                "imap_tls",
                "imap_user",
                "imap_folder",
                "imap_poll_interval_sec",
            ):
                kw.pop(f, None)
        cron_expr = kw.get("cron_expr")
        if row["type"] == "CRON":
            if not cron_expr:
                raise TriggerValidationError("CRON 触发器须给 cron_expr")
            try:
                parse_cron(str(cron_expr))
            except CronError as e:
                raise TriggerValidationError(str(e)) from e
            try:
                kw["next_fire_at"] = next_fire(str(cron_expr), _now()).isoformat()
            except CronError as e:
                raise TriggerValidationError(str(e)) from e
        # 040:仅目标变更时重校验——目标已删的存量触发器仍可停用/改非目标字段
        new_tt = str(kw.get("target_type", row["target_type"]))
        new_tk = str(kw.get("target_key", row["target_key"]))
        if (
            self._target_checker is not None
            and (new_tt != str(row["target_type"]) or new_tk != str(row["target_key"]))
            and not await self._target_checker.target_exists(
                target_type=new_tt,
                target_key=new_tk,
                tenant_id=tenant_id,
                creator_id=str(row["created_by"]),
            )
        ):
            raise TargetNotFound(new_tk)
        if not await self._repo.update(trigger_id=trigger_id, tenant_id=tenant_id, **kw):
            raise TriggerNotFound(trigger_id)
        await self._audit("trigger.update", str(row["tg_key"]), {})

    async def set_enabled(self, *, trigger_id: str, tenant_id: str, enabled: bool) -> None:
        if not await self._repo.set_enabled(
            trigger_id=trigger_id, tenant_id=tenant_id, enabled=enabled
        ):
            raise TriggerNotFound(trigger_id)
        if not enabled:
            # 101:停用语义=「不再主动执行」,挂起重试是未来时态的执行,一并取消
            # (收敛 FAILED 终态可见;重新启用不补做,从下个周期开始)
            await self._repo.cancel_pending_retries(
                trigger_id=trigger_id, reason="触发器已停用,挂起重试取消"
            )
        await self._audit(
            "trigger.enable" if enabled else "trigger.disable", trigger_id, {"enabled": enabled}
        )

    async def delete(self, *, trigger_id: str, tenant_id: str) -> None:
        if not await self._repo.delete(trigger_id=trigger_id, tenant_id=tenant_id):
            raise TriggerNotFound(trigger_id)
        # 101:定义删除即取消挂起重试(run 行随历史保留,不随定义消失)
        await self._repo.cancel_pending_retries(
            trigger_id=trigger_id, reason="触发器已删除,挂起重试取消"
        )
        await self._audit("trigger.delete", trigger_id, {})

    async def list_all(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return await self._repo.list_all(tenant_id=tenant_id)

    async def page_runs(
        self, *, tenant_id: str, trigger_id: str, page: int, size: int
    ) -> tuple[int, list[dict[str, Any]]]:
        # R1-S5:历史不随定义删除而不可读(查询列自带 tenant_id 隔离,
        # 空集即不存在;rowcount 语义下跨租户恒空,无存在性泄漏)
        return await self._repo.page_runs(
            tenant_id=tenant_id, trigger_id=trigger_id, page=page, size=size
        )

    # ── 执行(webhook 入站与调度共用) ──

    async def find_webhook_row(self, *, path: str) -> dict[str, Any] | None:
        return await self._repo.find_by_webhook_path(path=path)

    def verify_signature(self, *, row: dict[str, Any], body: bytes, signature: str) -> None:
        """HMAC 校验(请求期;compare_digest 防时序)。"""
        try:
            secret = self._cipher.decrypt(str(row["secret_cipher"]))
        except Exception as e:
            # R1-T5:密文损坏/密钥轮换——同形拒(不 500 泄漏内部态)
            raise WebhookRejected(f"secret 不可用: {type(e).__name__}") from e
        expect = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac_mod.compare_digest(expect, signature):
            raise WebhookRejected("signature")

    def normalize_inputs(
        self, *, row: dict[str, Any], body: bytes, event_header: str
    ) -> dict[str, Any]:
        """适配器标准化(请求期;畸形同形拒)。"""
        try:
            payload = _parse_json(body)
            return normalize(str(row["adapter"]), payload, event_header)
        except (AdapterError, ValueError) as e:
            raise WebhookRejected(f"payload 解析失败: {e}") from e

    async def fire_webhook(
        self, *, path: str, body: bytes, signature: str, event_header: str
    ) -> None:
        """后台执行(路由已在请求期完成校验;此处深防御复验一次)。

        summary_json=标准化 inputs JSON:重试回放载体(101,fire_retry 沿链首
        行还原),与 email 摘要同列同语义。"""
        if len(body) > _MAX_BODY:
            raise WebhookRejected("body 超 1MB")
        row = await self._repo.find_by_webhook_path(path=path)
        if row is None or not row["enabled"]:
            raise WebhookRejected(path)
        self.verify_signature(row=row, body=body, signature=signature)
        inputs = self.normalize_inputs(row=row, body=body, event_header=event_header)
        await self._fire(
            row,
            inputs=inputs,
            channel="webhook",
            summary_json=json.dumps(inputs, ensure_ascii=False),
        )

    async def fire_prepared(self, *, row: dict[str, Any], inputs: dict[str, Any]) -> None:
        """请求期已校验的直入(后台任务用);summary_json=回放载体(同 fire_webhook)。"""
        await self._fire(
            row,
            inputs=inputs,
            channel="webhook",
            summary_json=json.dumps(inputs, ensure_ascii=False),
        )

    async def fire_cron(self, *, trigger_id: str) -> None:
        """调度器调用:到期触发(min_interval/进行中 SKIPPED 由调度器判)。"""
        row = await self._repo.get_by_id(trigger_id=trigger_id)
        if row is None or not row["enabled"]:
            return
        await self._fire(row, inputs={}, channel="cron")

    async def list_due(self, *, now_iso: str) -> list[dict[str, Any]]:
        return await self._repo.list_due_cron(now_iso=now_iso)

    # ── 邮件入站(108) ──

    async def list_due_emails(self, *, now_iso: str) -> list[dict[str, Any]]:
        """调度器调用:轮询间隔到期的邮件触发器(停用即出扫描面)。"""
        return await self._repo.list_due_emails(now_iso=now_iso)

    async def mark_email_polled(self, *, trigger_id: str, interval_sec: int) -> None:
        """轮询节流推进(成功/失败/SKIPPED 共用):last_fired=now,
        next_fire=now+轮询间隔——失败不堆积,下个间隔再试。"""
        now = _now()
        await self._repo.mark_fired(
            trigger_id=trigger_id,
            last_fired_iso=now.isoformat(),
            next_fire_iso=(now + dt.timedelta(seconds=max(1, int(interval_sec)))).isoformat(),
        )
        await self._repo.commit()

    async def poll_email(self, *, trigger_id: str) -> None:
        """轮询一次邮件触发器(108,调度器调用)。

        语义链:UID 游标续传(不重复不漏)→ UIDVALIDITY 变更游标作废重扫
        (告警在案,宁重复不丢失)→ 每封新邮件经摘要白名单适配+输入护栏
        → 目标执行 → run 落库与游标推进同事务(至少一次;崩溃双回滚,
        下轮重触发,重复由 run 历史可见)。连接/认证/协议失败按错误分类
        落 FAILED run,不执行目标、不推进游标;任何异常不逃逸(单触发器
        失败不扩散)。目标执行失败走 101 重试链(形态与策略正交)。
        """
        row = await self._repo.get_by_id(trigger_id=trigger_id)
        if row is None or not row["enabled"] or str(row["type"]) != "EMAIL":
            return
        tenant_id = str(row["tenant_id"])
        folder = str(row.get("imap_folder") or "INBOX")
        cur = await self._repo.get_email_cursor(trigger_id=trigger_id, folder=folder)
        last_uid = int(cur["last_uid"]) if cur else 0
        stored_uv = int(cur["uidvalidity"]) if cur else None
        if self._mail is None:
            await self.record_failed(
                tenant_id=tenant_id,
                trigger_id=trigger_id,
                reason="MAIL_ERROR: 邮件客户端未接线(部署缺陷)",
            )
            return
        # 凭证解密失败(密钥轮换/密文损坏)同样分类落库,不 500 不逃逸
        credential = ""
        if row.get("imap_credential"):
            try:
                credential = self._cipher.decrypt(str(row["imap_credential"]))
            except Exception as e:  # noqa: BLE001
                await self.record_failed(
                    tenant_id=tenant_id,
                    trigger_id=trigger_id,
                    reason=f"CREDENTIAL_ERROR: {type(e).__name__}",
                )
                return
        fetch_kwargs: dict[str, Any] = {
            "host": str(row.get("imap_host") or ""),
            "port": int(row.get("imap_port") or 993),
            "tls": bool(row.get("imap_tls")),
            "user": str(row.get("imap_user") or ""),
            "credential": credential,
            "folder": folder,
            "limit": MAX_MESSAGES_PER_POLL,
        }
        try:
            fetch = await self._mail.fetch_since(last_uid=last_uid, **fetch_kwargs)
            reset = cur is not None and stored_uv is not None and stored_uv != fetch.uidvalidity
            if reset:
                # UIDVALIDITY 变更:游标作废重置(design:全文件夹重扫会重复
                # 触发——记录告警,人工裁决;宁重复不丢失的一致延续)
                log.warning(
                    "trigger %s 文件夹 %s UIDVALIDITY 变更 %s→%s,游标重置全量重扫",
                    trigger_id,
                    folder,
                    stored_uv,
                    fetch.uidvalidity,
                )
                fetch = await self._mail.fetch_since(last_uid=0, **fetch_kwargs)
                last_uid = 0
                await self._repo.upsert_email_cursor(
                    trigger_id=trigger_id, folder=folder, last_uid=0, uidvalidity=fetch.uidvalidity
                )
            elif cur is None:
                # 首次轮询:登记游标行(UIDVALIDITY 基准)
                await self._repo.upsert_email_cursor(
                    trigger_id=trigger_id, folder=folder, last_uid=0, uidvalidity=fetch.uidvalidity
                )
        except MailAuthError as e:
            await self.record_failed(
                tenant_id=tenant_id, trigger_id=trigger_id, reason=f"AUTH_FAILED: {e}"
            )
            return
        except MailConnectError as e:
            await self.record_failed(
                tenant_id=tenant_id, trigger_id=trigger_id, reason=f"CONNECT_FAILED: {e}"
            )
            return
        except MailError as e:
            await self.record_failed(
                tenant_id=tenant_id, trigger_id=trigger_id, reason=f"PROTOCOL_ERROR: {e}"
            )
            return
        except Exception as e:  # noqa: BLE001  # 邮件栈未知异常同样分类收敛,永不逃逸
            await self.record_failed(
                tenant_id=tenant_id,
                trigger_id=trigger_id,
                reason=f"MAIL_ERROR: {type(e).__name__}: {str(e)[:200]}",
            )
            return
        for m in sorted(fetch.messages, key=lambda x: x.uid):
            if m.uid <= last_uid:
                # IMAP `n:*` 语义:范围端点可反转(无更新时服务器仍回最高 UID
                # 一封)——严格大于游标才处理,幂等防线在调用侧
                continue
            summary = normalize_email(m.data)
            if self._input_guard is not None:
                blocked, processed = await self._input_guard.check(
                    tenant_id=tenant_id,
                    user_id=str(row["created_by"]),
                    texts=[summary["from"], summary["subject"], summary["text_preview"]],
                )
            else:
                blocked, processed = (
                    False,
                    [summary["from"], summary["subject"], summary["text_preview"]],
                )
            if blocked:
                # 拦截:目标不执行、摘要零留存(恶意内容不落库);游标照推进
                # 防同邮件死循环拦截(邮件已消费,审计在护栏审计表)
                await self._repo.insert_run(
                    tenant_id=tenant_id,
                    trigger_id=trigger_id,
                    status="FAILED",
                    error=f"输入护栏拦截: 邮件已消费(UID {m.uid}),目标未执行",
                    duration_ms=0,
                    target_run_ref="",
                    summary="",
                )
            else:
                masked = {
                    "from": processed[0],
                    "subject": processed[1],
                    "date": summary["date"],
                    "text_preview": processed[2],
                }
                # 触发记录与游标推进同事务(至少一次语义):_fire 收尾 commit
                await self._fire(
                    row,
                    inputs=dict(masked),
                    channel="email",
                    summary_json=json.dumps(masked, ensure_ascii=False),
                )
            await self._repo.upsert_email_cursor(
                trigger_id=trigger_id, folder=folder, last_uid=m.uid, uidvalidity=fetch.uidvalidity
            )
            if blocked:
                await self._repo.commit()  # 拦截路径无 _fire 收尾,显式同事务提交
        # 末封邮件的游标推进收尾提交(评审修复:_fire 的 commit 先于本封游标
        # upsert,末封游标原依赖调度器节流提交搭车——节流提交失败被吞时
        # close() 回滚即丢游标,下轮确定性重复触发末封邮件)
        await self._repo.commit()

    async def list_due_retries(self, *, now_iso: str) -> list[dict[str, Any]]:
        """调度器调用:到期挂起重试(101 调度重排扫描面)。"""
        return await self._repo.list_due_retries(now_iso=now_iso)

    async def fire_retry(self, *, run_id: str) -> None:
        """调度器调用:派发到期的挂起重试(101)。

        与 cron 同路:同身份(定义创建者)、同防御层(TargetRunner 组合根)、
        同目标解析口径;行已被取消/已派发时幂等返回。输入与渠道按触发器
        形态回放(见派发段注释)。
        """
        run = await self._repo.get_run(run_id=run_id)
        if run is None or run["status"] != "PENDING":
            return  # 已取消/已落定(幂等,防双派发)
        row = await self._repo.get_by_id(trigger_id=str(run["trigger_id"]))
        if row is None or not row["enabled"]:
            # 停用/删除的派发期兜底(取消钩子之外的竞态窗口):收敛取消
            await self._repo.cancel_pending_retries(
                trigger_id=str(run["trigger_id"]), reason="触发器已停用或删除,挂起重试取消"
            )
            await self._repo.commit()
            return
        # 重试回放(101:design「webhook/email 失败重试同策略适用」):原输入
        # 取自链首行 summary(fire_webhook 落标准化 inputs JSON;poll_email 落
        # 护栏后摘要,108 隐私口径不变),渠道按触发器形态还原——空 payload
        # 执行目标会以无意义执行掩盖真实失败;解析失败按空输入退回(不因脏
        # 摘要拒绝重试,与旧形态等价)。
        replay: dict[str, Any] = {}
        root_id = str(run.get("retry_of_run_id") or "")
        if root_id:
            root = await self._repo.get_run(run_id=root_id)
            raw = str((root or {}).get("summary") or "")
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                replay = cast("dict[str, Any]", parsed)
        channel = {"EMAIL": "email", "WEBHOOK": "webhook"}.get(str(row["type"]), "cron")
        await self._fire(row, inputs=replay, channel=channel, pending_run=run)

    async def defer_retry(self, *, run_id: str) -> None:
        """调度器调用:重试到期撞上在途执行 → 顺延一个退避间隔(永不并发)。"""
        await self._repo.defer_retry(
            run_id=run_id,
            next_retry_at=_now() + dt.timedelta(seconds=self._retry_backoff_sec),
        )
        await self._repo.commit()

    async def get_def(self, *, trigger_id: str) -> dict[str, Any] | None:
        return await self._repo.get_by_id(trigger_id=trigger_id)

    async def mark_fired(
        self, *, trigger_id: str, last_fired_iso: str, next_fire_iso: str | None
    ) -> None:
        await self._repo.mark_fired(
            trigger_id=trigger_id,
            last_fired_iso=last_fired_iso,
            next_fire_iso=next_fire_iso,
        )
        await self._repo.commit()

    async def record_failed(self, *, tenant_id: str, trigger_id: str, reason: str) -> None:
        """R1-S1:调度器止损落 FAILED(cron 重算失败等)。"""
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
        await self._repo.insert_run(
            tenant_id=tenant_id,
            trigger_id=trigger_id,
            status="SKIPPED",
            error=reason,
            duration_ms=0,
            target_run_ref="",
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
        """目标执行+run 落库+重试链推进+出站通知。任何失败收敛 FAILED 落库。

        101 重试链:普通触发落链首行(attempt=1);挂起重试派发在原 PENDING 行
        落定终态(不覆盖先前失败行)。FAILED 且剩余次数>0 → 追加一行挂起重试
        (链未终态,不通知);链终态(成功/耗尽/未配重试)→ 通知恰一次。
        summary_json(108/101 回放载体):邮件=护栏后标准化摘要(原文不落库,
        隐私最小化);webhook=标准化 inputs JSON。随链首行落库,fire_retry
        沿链首还原重放输入。
        """
        started = _now()
        attempt = int(pending_run["attempt"]) if pending_run is not None else 1
        status = "SUCCESS"
        error = ""
        ref = ""
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
            )
            if not ok:
                status = "FAILED"
                error = err
        except Exception as e:  # noqa: BLE001  # 触发执行永不逃逸
            status = "FAILED"
            error = f"{type(e).__name__}: {str(e)[:400]}"
        duration = int((_now() - started).total_seconds() * 1000)
        # 101 重试判定(纯函数,单源):FAILED 且剩余次数>0 → 安排;否则链终态
        retry_at = schedule_retry(
            status=status,
            attempt=attempt,
            max_retries=int(row.get("max_retries") or 0),
            now=_now(),
            backoff_sec=self._retry_backoff_sec,
        )
        if retry_at is None:
            # 链终态(成功/耗尽/未配重试):出站通知恰一次(max_retries=0 时
            # 即既有行为——执行落定即通知,零回归)
            notify_err = await self._notify(row, status=status, error=error, inputs=inputs)
            if notify_err:
                error = (error + ";" if error else "") + f"通知失败: {notify_err}"
        chain_root: str | None = None
        if pending_run is None:
            # 普通触发:落链首行(attempt=1);id 留作后续挂起行的链首关联
            chain_root = await self._repo.insert_run(
                tenant_id=str(row["tenant_id"]),
                trigger_id=str(row["id"]),
                status=status,
                error=error,
                duration_ms=duration,
                target_run_ref=ref,
                summary=summary_json,
            )
        else:
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
            if landed == 0:
                # 派发期间行被停用/删除取消(cancel_pending_retries 竞态):
                # 本次结果补记为独立行(通知已按真实 status 发出,结果不丢);
                # 不续链——用户取消语义优先,评审修复(原实现 no-op 后仍插
                # 新 PENDING 行,已停用触发器上多出挂起记录)
                await self._repo.insert_run(
                    tenant_id=str(row["tenant_id"]),
                    trigger_id=str(row["id"]),
                    status=status,
                    error=f"链已取消,结果补记: {error}" if error else "链已取消,结果补记",
                    duration_ms=duration,
                    target_run_ref=ref,
                    attempt=attempt,
                    retry_of_run_id=chain_root,
                )
                await self._repo.commit()
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

    async def _notify(
        self, row: dict[str, Any], *, status: str, error: str, inputs: dict[str, Any]
    ) -> str:
        """出站通知(SSRF 判定复用 029;签名 X-Kongming-Signature;5s 超时);
        返回错误文案(空=成功/未配置)。"""
        url = str(row.get("notify_url") or "")
        if not url:
            return ""
        if self._notifier is None:
            return "通知器未接线(部署缺陷)"
        try:
            await self._notifier.check_url(url)
        except Exception as e:
            return f"SSRF 拒绝: {e}"
        import json as _json

        body = _json.dumps(
            {
                "trigger": row["tg_key"],
                "status": status,
                "error": error,
                "finished_at": _now().isoformat(),
            },
            ensure_ascii=False,
        ).encode()
        secret = ""
        if row.get("secret_cipher"):
            # 解密失败(密钥轮换后旧密文/密文损坏)必须收敛为通知错误文案:
            # 此处抛出会逃出 _fire 的 try(通知在执行段之外)→ insert_run 被
            # 跳过+next_fire 不推进 → 调度器每 30s 无限重触发且零 run 零审计
            # (审查循环 F1,与入站侧 R1-T5 密钥轮换场景对称设防)
            try:
                secret = self._cipher.decrypt(str(row["secret_cipher"]))
            except Exception as e:  # noqa: BLE001
                return f"签名密钥解密失败(须轮换重存 secret): {type(e).__name__}"
        sig = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
        try:
            await self._notifier.post(url=url, body=body, signature=sig)
        except Exception as e:  # noqa: BLE001  # 通知失败不掩执行结果
            return f"{type(e).__name__}: {str(e)[:200]}"
        return ""


def _parse_json(body: bytes) -> dict[str, Any]:
    import json

    parsed = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(parsed, dict):
        raise ValueError("payload 须为 JSON 对象")
    return cast("dict[str, Any]", parsed)
