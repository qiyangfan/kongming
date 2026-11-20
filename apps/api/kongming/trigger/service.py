"""trigger 用例门面:时区与日志口径单源,对外组合管理/入站/邮件三簇用例。

定义管理在 definition.py(校验规则见 validation.py,执行/落库/通知在
firing.py)、webhook 入站与 task 目标闸在 ingress.py、邮件轮询在
email.py。用例层不依赖 FastAPI 或 SQLAlchemy;时区固定 Asia/Shanghai。
邮件入站形态——UID 游标轮询 + 摘要白名单适配 + 输入护栏三层
(轮询器是「形态」,重试是「策略」,正交共存)。"""

import datetime as dt
import json
from typing import Any

from kongming.trigger.definition import TriggerDefinitionService
from kongming.trigger.email import TriggerEmailService
from kongming.trigger.errors import (  # 对外再导出:路由/调度按 service 寻址
    TargetNotFound,
    TriggerKeyTaken,
    TriggerNotFound,
    TriggerValidationError,
    WebhookRejected,
)
from kongming.trigger.firing import FiringCore, TriggerOperLogger, replay_inputs
from kongming.trigger.ingress import TriggerIngressService
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
from kongming.trigger.retry import DEFAULT_BACKOFF_SEC
from kongming.trigger.task_target import (  # 对外再导出:既有调用方按 service 寻址
    filter_pass,
    render_goal,
    thread_key,
)
from kongming.trigger.validation import (  # 对外再导出:既有调用方按 service 寻址
    MAX_MESSAGES_PER_POLL,
    MIN_POLL_INTERVAL_SEC,
    TASK_TARGET,
    TG_KEY_RE,
    now,
    validate_task_template,
)

__all__ = [
    "MAX_MESSAGES_PER_POLL",
    "MIN_POLL_INTERVAL_SEC",
    "TASK_TARGET",
    "TG_KEY_RE",
    "TargetNotFound",
    "TriggerKeyTaken",
    "TriggerNotFound",
    "TriggerOperLogger",
    "TriggerService",
    "TriggerValidationError",
    "WebhookRejected",
    "filter_pass",
    "render_goal",
    "thread_key",
    "validate_task_template",
]


class _CronFiring(FiringCore):
    """调度侧执行:cron 到期触发与挂起重试(与入站/邮件共用执行核心)。"""

    # ── 调度侧执行(cron 与挂起重试) ──

    async def fire_cron(self, *, trigger_id: str) -> None:
        """调度器调用:到期触发(min_interval/进行中 SKIPPED 由调度器判)。"""
        row = await self._repo.get_by_id(trigger_id=trigger_id)
        if row is None or not row["enabled"]:
            return
        await self._fire(row, inputs={}, channel="cron")

    async def list_due(self, *, now_iso: str) -> list[dict[str, Any]]:
        return await self._repo.list_due_cron(now_iso=now_iso)

    async def list_due_retries(self, *, now_iso: str) -> list[dict[str, Any]]:
        """调度器调用:到期挂起重试(调度重排扫描面)。"""
        return await self._repo.list_due_retries(now_iso=now_iso)

    async def fire_retry(self, *, run_id: str) -> None:
        """调度器调用:派发到期的挂起重试。

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
        replay = await self._replay_inputs(run)
        channel = {"EMAIL": "email", "WEBHOOK": "webhook"}.get(str(row["type"]), "cron")
        await self._fire(row, inputs=replay, channel=channel, pending_run=run)

    async def _replay_inputs(self, run: dict[str, Any]) -> dict[str, Any]:
        """重试回放输入:取链首 summary 并解析。

        链首 summary 由 fire_webhook 落标准化 inputs JSON、poll_email 落护栏后
        摘要(隐私口径不变);解析失败按空输入退回(不因脏摘要拒绝重试)。"""
        root_id = str(run.get("retry_of_run_id") or "")
        if not root_id:
            return {}
        root = await self._repo.get_run(run_id=root_id)
        raw = str((root or {}).get("summary") or "")
        parsed: object = None
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
        return replay_inputs(parsed)

    async def defer_retry(self, *, run_id: str) -> None:
        """调度器调用:重试到期撞上在途执行 → 顺延一个退避间隔(永不并发)。"""
        await self._repo.defer_retry(
            run_id=run_id,
            next_retry_at=now() + dt.timedelta(seconds=self._retry_backoff_sec),
        )
        await self._repo.commit()

    async def get_def(self, *, trigger_id: str) -> dict[str, Any] | None:
        return await self._repo.get_by_id(trigger_id=trigger_id)


class TriggerService(
    TriggerDefinitionService, TriggerIngressService, _CronFiring, TriggerEmailService
):
    """触发器用例门面:管理面 + webhook 入站 + 邮件轮询 + 调度执行。

    target_checker 属定义写入边界;其余端口由各簇各自消费。"""

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
        event_judge: EventJudge | None = None,
        task_gateway: TaskGateway | None = None,
    ) -> None:
        super().__init__(
            repo=repo,
            cipher=cipher,
            runner=runner,
            notifier=notifier,
            oper=oper,
            retry_backoff_sec=retry_backoff_sec,
            mail=mail,
            input_guard=input_guard,
            event_judge=event_judge,
            task_gateway=task_gateway,
        )
        self._target_checker = target_checker
