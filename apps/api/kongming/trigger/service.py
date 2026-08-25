"""trigger 用例编排(031):CRUD 校验 + 目标执行 + 出站通知 +
执行历史。零框架(宪法 Article II);时区固定 Asia/Shanghai。"""

import datetime as dt
import hashlib
import hmac as hmac_mod
import re
import uuid as uuid_mod
from typing import Any, Protocol

from kongming.trigger.adapters import AdapterError, normalize
from kongming.trigger.models import CronError, next_fire, parse_cron
from kongming.trigger.ports import Notifier, SecretCipher, TargetRunner, TriggerRepo

TG_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Shanghai(design O5 挂账可配)
_MAX_BODY = 1 * 1024 * 1024


class TriggerValidationError(Exception):
    """领域层 400(key/cron/目标形态)。"""


class TriggerNotFound(Exception):
    """不存在/跨租户同形。"""


class TriggerKeyTaken(Exception):
    """同租户 tg_key 冲突(409 语义,R1-S2)。"""


class WebhookRejected(Exception):
    """签名校验失败/禁用/不存在——同形(防探测)。"""


class TriggerOperLogger(Protocol):
    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


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
    ) -> None:
        self._repo = repo
        self._cipher = cipher
        self._runner = runner
        self._notifier = notifier
        self._oper = oper

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
    ) -> dict[str, Any]:
        if not TG_KEY_RE.fullmatch(tg_key):
            raise TriggerValidationError("tg_key 须为小写字母开头的字母/数字/下划线(2-64 位)")
        if not name.strip() or len(name) > 128:
            raise TriggerValidationError("名称非空且 ≤128 字符")
        if type_ not in ("CRON", "WEBHOOK"):
            raise TriggerValidationError("type 须为 CRON/WEBHOOK")
        if target_type not in ("workflow", "bot"):
            raise TriggerValidationError("target_type 须为 workflow/bot")
        if adapter not in ("custom", "github"):
            raise TriggerValidationError("adapter 须为 custom/github")
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
        next_fire_iso: str | None = None
        if type_ == "CRON" and cron_expr:
            try:
                next_fire_iso = next_fire(cron_expr, _now()).isoformat()
            except CronError as e:
                # R1-S1a:计算失败(永不触发/366 天外)同属保存期 400
                raise TriggerValidationError(str(e)) from e
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
        if not await self._repo.update(trigger_id=trigger_id, tenant_id=tenant_id, **kw):
            raise TriggerNotFound(trigger_id)
        await self._audit("trigger.update", str(row["tg_key"]), {})

    async def set_enabled(self, *, trigger_id: str, tenant_id: str, enabled: bool) -> None:
        if not await self._repo.set_enabled(
            trigger_id=trigger_id, tenant_id=tenant_id, enabled=enabled
        ):
            raise TriggerNotFound(trigger_id)
        await self._audit(
            "trigger.enable" if enabled else "trigger.disable", trigger_id, {"enabled": enabled}
        )

    async def delete(self, *, trigger_id: str, tenant_id: str) -> None:
        if not await self._repo.delete(trigger_id=trigger_id, tenant_id=tenant_id):
            raise TriggerNotFound(trigger_id)
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
        """后台执行(路由已在请求期完成校验;此处深防御复验一次)。"""
        if len(body) > _MAX_BODY:
            raise WebhookRejected("body 超 1MB")
        row = await self._repo.find_by_webhook_path(path=path)
        if row is None or not row["enabled"]:
            raise WebhookRejected(path)
        self.verify_signature(row=row, body=body, signature=signature)
        inputs = self.normalize_inputs(row=row, body=body, event_header=event_header)
        await self._fire(row, inputs=inputs, channel="webhook")

    async def fire_prepared(self, *, row: dict[str, Any], inputs: dict[str, Any]) -> None:
        """请求期已校验的直入(后台任务用)。"""
        await self._fire(row, inputs=inputs, channel="webhook")

    async def fire_cron(self, *, trigger_id: str) -> None:
        """调度器调用:到期触发(min_interval/进行中 SKIPPED 由调度器判)。"""
        row = await self._repo.get_by_id(trigger_id=trigger_id)
        if row is None or not row["enabled"]:
            return
        await self._fire(row, inputs={}, channel="cron")

    async def list_due(self, *, now_iso: str) -> list[dict[str, Any]]:
        return await self._repo.list_due_cron(now_iso=now_iso)

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
        self, row: dict[str, Any], *, inputs: dict[str, Any], channel: str
    ) -> None:
        """目标执行+run 落库+出站通知。任何失败收敛 FAILED 落库。"""
        started = _now()
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
        notify_err = await self._notify(row, status=status, error=error, inputs=inputs)
        if notify_err:
            error = (error + ";" if error else "") + f"通知失败: {notify_err}"
        await self._repo.insert_run(
            tenant_id=str(row["tenant_id"]),
            trigger_id=str(row["id"]),
            status=status,
            error=error,
            duration_ms=duration,
            target_run_ref=ref,
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
    return parsed
