"""触发器定义管理面用例:创建/更新/启停/删除/列表与执行历史分页。"""

from dataclasses import dataclass
from typing import Any, cast

from kongming.trigger.errors import (
    TargetNotFound,
    TriggerKeyTaken,
    TriggerNotFound,
    TriggerValidationError,
)
from kongming.trigger.firing import FiringCore, generated_secret, new_webhook_path
from kongming.trigger.ports import TargetChecker
from kongming.trigger.validation import (
    MIN_POLL_INTERVAL_SEC,
    TASK_TARGET,
    TG_KEY_RE,
    bounded_str_len,
    check_imap_create,
    next_fire_iso_or_400,
    now,
    parse_cron_or_400,
    prepare_imap_update,
    strip_imap_fields,
    validate_task_template,
)


@dataclass(frozen=True)
class TriggerDraft:
    """创建触发器的输入字段(与 TriggerIn 同形;组合根装配)。"""

    tenant_id: str
    tg_key: str
    name: str
    type_: str
    target_type: str
    target_key: str
    cron_expr: str | None
    min_interval_sec: int
    adapter: str
    notify_url: str
    secret: str
    created_by: str
    max_retries: int = 0
    task_template: dict[str, Any] | None = None
    imap_host: str = ""
    imap_port: int = 993
    imap_tls: bool = True
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    imap_poll_interval_sec: int = 300


class TriggerDefinitionService(FiringCore):
    """定义管理:管理面写操作与查询(目标存在性 checker 未注入=跳过写边界闸)。"""

    # 写边界闸端口(组合根绑定)
    _target_checker: TargetChecker | None = None

    async def create(self, draft: TriggerDraft) -> dict[str, Any]:
        """创建触发器:保存期校验 → 目标存在性闸 → 重名预检 → 落库与审计。

        返回值 {id, webhook_path} 外带 webhook secret 的一次性明文展示。"""
        self._check_basic(
            tg_key=draft.tg_key,
            name=draft.name,
            type_=draft.type_,
            target_type=draft.target_type,
            target_key=draft.target_key,
            adapter=draft.adapter,
        )
        task_template = self._create_template(
            draft.target_type, draft.target_key, draft.task_template
        )
        webhook_path: str | None = None
        plain_secret_out: str | None = None
        next_fire_iso: str | None = None
        imap_credential_cipher = ""
        secret = draft.secret
        imap_poll_interval_sec = draft.imap_poll_interval_sec
        if draft.type_ == "WEBHOOK":
            webhook_path = new_webhook_path()
            if not secret:
                secret = generated_secret()  # 缺省生成(一次性展示)
            plain_secret_out = secret
        if draft.type_ == "EMAIL":
            check_imap_create(
                draft.imap_host, draft.imap_user, draft.imap_password, draft.imap_port
            )
            imap_poll_interval_sec = max(MIN_POLL_INTERVAL_SEC, int(draft.imap_poll_interval_sec))
            imap_credential_cipher = self._cipher.encrypt(draft.imap_password)
            next_fire_iso = now().isoformat()  # 创建即到期,首个 tick 轮询
        if draft.type_ == "CRON":
            parse_cron_or_400(draft.cron_expr)
            next_fire_iso = next_fire_iso_or_400(draft.cron_expr)
        await self._assert_target_create(
            draft.target_type, draft.target_key, task_template, draft.tenant_id, draft.created_by
        )
        # 重名预检(唯一索引兜底之外的友好 409,与 datasource 同范式)
        if await self._repo.get(tenant_id=draft.tenant_id, tg_key=draft.tg_key) is not None:
            raise TriggerKeyTaken(draft.tg_key)
        tid = await self._repo.create(
            tenant_id=draft.tenant_id,
            tg_key=draft.tg_key,
            name=draft.name.strip(),
            type=draft.type_,
            target_type=draft.target_type,
            target_key=draft.target_key,
            cron_expr=draft.cron_expr,
            min_interval_sec=draft.min_interval_sec,
            webhook_path=webhook_path,
            secret_cipher=self._cipher.encrypt(secret) if secret else "",
            adapter=draft.adapter,
            notify_url=draft.notify_url,
            created_by=draft.created_by,
            next_fire_at=next_fire_iso,
            max_retries=draft.max_retries,
            imap_host=draft.imap_host.strip(),
            imap_port=int(draft.imap_port),
            imap_tls=bool(draft.imap_tls),
            imap_user=draft.imap_user.strip(),
            imap_credential=imap_credential_cipher,
            imap_folder=draft.imap_folder if draft.type_ == "EMAIL" else "INBOX",
            imap_poll_interval_sec=int(imap_poll_interval_sec),
            task_template=task_template,
        )
        await self._audit(
            "trigger.create", draft.tg_key, {"type": draft.type_, "target": draft.target_key}
        )
        out: dict[str, Any] = {"id": tid, "webhook_path": webhook_path}
        if plain_secret_out:
            out["secret"] = plain_secret_out  # 创建一次性展示(与 api-key 同哲学)
        return out

    def _check_basic(
        self,
        *,
        tg_key: str,
        name: str,
        type_: str,
        target_type: str,
        target_key: str,
        adapter: str,
    ) -> None:
        """创建期字段值域校验(顺序即错误优先级)。"""
        if not TG_KEY_RE.fullmatch(tg_key):
            raise TriggerValidationError("tg_key 须为小写字母开头的字母/数字/下划线(2-64 位)")
        if not name.strip() or not bounded_str_len(name, 1, 128):
            raise TriggerValidationError("名称非空且 ≤128 字符")
        if type_ not in ("CRON", "WEBHOOK", "EMAIL"):
            raise TriggerValidationError("type 须为 CRON/WEBHOOK/EMAIL")
        if target_type not in ("workflow", "bot", TASK_TARGET):
            raise TriggerValidationError("target_type 须为 workflow/bot/task")
        if adapter not in ("custom", "github"):
            raise TriggerValidationError("adapter 须为 custom/github")

    def _create_template(
        self, target_type: str, target_key: str, task_template: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """task 目标以模板取代 target_key 引用:模板保存期校验 fail-fast;
        非 task 目标模板必须为空(两形态互斥,不留歧义配置)。"""
        if target_type == TASK_TARGET:
            if target_key.strip():
                raise TriggerValidationError("task 目标不受理 target_key(配置在任务模板)")
            return validate_task_template(task_template)
        if task_template is not None:
            raise TriggerValidationError("仅 target_type=task 受理任务模板")
        if not target_key.strip():
            raise TriggerValidationError("workflow/bot 目标须给 target_key")
        return None

    async def _assert_target_create(
        self,
        target_type: str,
        target_key: str,
        task_template: dict[str, Any] | None,
        tenant_id: str,
        created_by: str,
    ) -> None:
        """写边界闸:目标不存在即 400,防拼错目标静默挂到运行期才暴露;
        task 目标校验对象=模板数据源(对创建者可见性)。"""
        if self._target_checker is None:
            return
        checker_key = (
            str(task_template["datasource_key"])
            if target_type == TASK_TARGET and task_template is not None
            else target_key
        )
        if not await self._target_checker.target_exists(
            target_type=target_type,
            target_key=checker_key,
            tenant_id=tenant_id,
            creator_id=created_by,
        ):
            raise TargetNotFound(checker_key)

    async def update(self, *, trigger_id: str, tenant_id: str, **kw: Any) -> None:
        """更新触发器:按形态规整/校验待改字段,目标变更时复验存在性,再落库审计。"""
        row = await self._repo.get_by_id(trigger_id=trigger_id)
        if row is None or str(row["tenant_id"]) != tenant_id:
            raise TriggerNotFound(trigger_id)
        self._apply_secret(kw)
        # 邮件配置列仅 EMAIL 形态更新(空框=凭证保持现值;CRON/WEBHOOK
        # 行剥除邮件字段,防误覆盖);配置变更即到期,下个 tick 按新配置轮询
        if str(row["type"]) == "EMAIL":
            self._apply_email_update(kw)
        else:
            strip_imap_fields(kw)
        self._apply_cron_update(row, kw)
        new_tt, new_tk = self._apply_target_update(row, kw)
        await self._assert_target_update(row, tenant_id, new_tt, new_tk, kw.get("task_template"))
        if not await self._repo.update(trigger_id=trigger_id, tenant_id=tenant_id, **kw):
            raise TriggerNotFound(trigger_id)
        await self._audit("trigger.update", str(row["tg_key"]), {})

    def _apply_secret(self, kw: dict[str, Any]) -> None:
        """密钥轮换(仅显式非空才重加密;空=保持现值)。"""
        new_secret = str(kw.pop("secret", "") or "")
        if new_secret:
            kw["secret_cipher"] = self._cipher.encrypt(new_secret)

    def _apply_email_update(self, kw: dict[str, Any]) -> None:
        """EMAIL 行更新:规整邮件配置并置即刻到期(空口令框=凭证保持现值)。"""
        imap_password = prepare_imap_update(kw)
        if imap_password:
            kw["imap_credential"] = self._cipher.encrypt(imap_password)
        kw["next_fire_at"] = now().isoformat()

    def _apply_cron_update(self, row: dict[str, Any], kw: dict[str, Any]) -> None:
        """CRON 行更新:表达式校验并重算下次触发。"""
        if row["type"] != "CRON":
            return
        cron_expr = kw.get("cron_expr")
        parse_cron_or_400(cron_expr)
        kw["next_fire_at"] = next_fire_iso_or_400(cron_expr)

    def _apply_target_update(self, row: dict[str, Any], kw: dict[str, Any]) -> tuple[str, str]:
        """目标字段规整:task 模板保存期校验(与创建同源),非 task 模板置空。"""
        new_tt = str(kw.get("target_type", row["target_type"]))
        new_tk = str(kw.get("target_key", row["target_key"]))
        tpl_in = kw.get("task_template")
        if new_tt == TASK_TARGET:
            kw["task_template"] = validate_task_template(tpl_in)
            new_tk = ""
        else:
            kw["task_template"] = None
            if tpl_in is not None:
                raise TriggerValidationError("仅 target_type=task 受理任务模板")
            if not new_tk.strip() and "target_key" in kw:
                raise TriggerValidationError("workflow/bot 目标须给 target_key")
        return new_tt, new_tk

    async def _assert_target_update(
        self,
        row: dict[str, Any],
        tenant_id: str,
        new_tt: str,
        new_tk: str,
        new_tpl: object,
    ) -> None:
        """仅目标变更时重校验——目标已删的存量触发器仍可停用/改非目标字段。"""
        changed = (
            new_tt != str(row["target_type"])
            or new_tk != str(row["target_key"])
            or new_tpl != row.get("task_template")
        )
        if self._target_checker is None or not changed:
            return
        checker_key = str(cast("dict[str, Any]", new_tpl)["datasource_key"]) if new_tpl else new_tk
        if not await self._target_checker.target_exists(
            target_type=new_tt,
            target_key=checker_key,
            tenant_id=tenant_id,
            creator_id=str(row["created_by"]),
        ):
            raise TargetNotFound(checker_key)

    async def set_enabled(self, *, trigger_id: str, tenant_id: str, enabled: bool) -> None:
        if not await self._repo.set_enabled(
            trigger_id=trigger_id, tenant_id=tenant_id, enabled=enabled
        ):
            raise TriggerNotFound(trigger_id)
        if not enabled:
            # 停用语义=「不再主动执行」,挂起重试是未来时态的执行,一并取消
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
        # 定义删除即取消挂起重试(run 行随历史保留,不随定义消失)
        await self._repo.cancel_pending_retries(
            trigger_id=trigger_id, reason="触发器已删除,挂起重试取消"
        )
        await self._audit("trigger.delete", trigger_id, {})

    async def list_all(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return await self._repo.list_all(tenant_id=tenant_id)

    async def page_runs(
        self, *, tenant_id: str, trigger_id: str, page: int, size: int
    ) -> tuple[int, list[dict[str, Any]]]:
        # 历史不随定义删除而不可读(查询列自带 tenant_id 隔离,
        # 空集即不存在;rowcount 语义下跨租户恒空,无存在性泄漏)
        return await self._repo.page_runs(
            tenant_id=tenant_id, trigger_id=trigger_id, page=page, size=size
        )
