"""命令执行器:有限动作枚举 + 版本/参数校验 + 幂等回执。

跨服务命令来自控制面队列;本地运行命令走同一执行器但不经队列,控制面缺席
仍可用。执行器只接受注册表内的 action,任意 SQL/脚本没有入口。
"""

import logging
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from kongming.control.models import CommandEnvelope, CommandOutcome, CommandRejected
from kongming.control.ports import AuditPort, CommandReceiptStore

log = logging.getLogger(__name__)

ParamType = Literal["str", "int", "str?"]


@dataclass(frozen=True)
class ActionSpec:
    action: str
    version: int
    params: dict[str, ParamType]
    description: str


class ActionHandler(Protocol):
    async def __call__(self, params: dict[str, object], *, actor: str) -> dict[str, object]: ...


ACTION_SPECS: dict[str, ActionSpec] = {
    "rag.reindex": ActionSpec(
        action="rag.reindex",
        version=1,
        params={"tenant_key": "str", "datasource_key": "str"},
        description="重建指定租户/数据源的 RAG 表描述与向量",
    ),
    "cache.clear": ActionSpec(
        action="cache.clear",
        version=1,
        params={"tenant_key": "str", "datasource_key": "str"},
        description="清空指定租户/数据源的检索上下文与 schema 图缓存",
    ),
    "task.cancel": ActionSpec(
        action="task.cancel",
        version=1,
        params={"tenant_key": "str", "task_id": "str", "reason": "str?"},
        description="取消租户内指定任务",
    ),
}


def _validate(spec: ActionSpec, params: dict[str, object]) -> dict[str, object]:
    unknown = set(params) - set(spec.params)
    if unknown:
        raise CommandRejected(f"命令参数含未知键: {sorted(unknown)}")
    cleaned: dict[str, object] = {}
    for name, kind in spec.params.items():
        value = params.get(name)
        optional = kind.endswith("?")
        base = kind.rstrip("?")
        if value is None:
            if optional:
                continue
            raise CommandRejected(f"命令参数缺 {name}")
        if base == "str":
            if not isinstance(value, str) or not value.strip():
                raise CommandRejected(f"命令参数 {name} 必须为非空字符串")
            cleaned[name] = value.strip()
        elif base == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise CommandRejected(f"命令参数 {name} 必须为整数")
            cleaned[name] = value
    return cleaned


class CommandExecutor:
    def __init__(
        self,
        *,
        receipts: CommandReceiptStore,
        handlers: dict[str, ActionHandler],
        audit: AuditPort,
    ) -> None:
        self._receipts = receipts
        self._handlers = handlers
        self._audit = audit

    def spec(self, action: str) -> ActionSpec | None:
        return ACTION_SPECS.get(action)

    async def execute(
        self,
        *,
        command_id: str,
        idempotency_key: str,
        action: str,
        action_version: int,
        params: dict[str, object],
        actor: str,
        source: str,
        reason: str = "",
    ) -> CommandOutcome:
        prepared = self._prepare(action, action_version, params)
        if isinstance(prepared, CommandOutcome):
            return prepared
        spec, cleaned = prepared
        prior = await self._receipts.get(idempotency_key=idempotency_key)
        if prior is not None:
            return prior
        claimed = await self._receipts.begin(
            idempotency_key=idempotency_key,
            command_id=command_id,
            action=action,
            action_version=action_version,
            source=source,
            actor=actor,
        )
        if not claimed:
            return CommandOutcome(status="FAILED", error="同幂等键命令正在执行,拒绝重复执行")
        outcome = await self._run_handler(spec, cleaned, actor)
        await self._receipts.finish(
            idempotency_key=idempotency_key,
            status=outcome.status,
            result={"result": outcome.result, "error": outcome.error},
        )
        await self._audit.log(
            actor=actor or None,
            action="control.command",
            target=f"{action}@{action_version}",
            detail={
                "command_id": command_id,
                "idempotency_key": idempotency_key,
                "source": source,
                "status": outcome.status,
                "error": outcome.error,
                "reason": reason[:500],
            },
        )
        return outcome

    def _prepare(
        self, action: str, action_version: int, params: dict[str, object]
    ) -> "tuple[ActionSpec, dict[str, object]] | CommandOutcome":
        spec = ACTION_SPECS.get(action)
        if spec is None or spec.version != action_version:
            return CommandOutcome(
                status="REJECTED",
                error=f"未知 action/版本: {action}@{action_version}",
            )
        try:
            return spec, _validate(spec, params)
        except CommandRejected as e:
            return CommandOutcome(status="REJECTED", error=str(e))

    async def _run_handler(
        self, spec: ActionSpec, params: dict[str, object], actor: str
    ) -> CommandOutcome:
        handler = self._handlers.get(spec.action)
        if handler is None:
            return CommandOutcome(status="FAILED", error=f"命令处理器未装配: {spec.action}")
        try:
            return CommandOutcome(status="SUCCEEDED", result=await handler(params, actor=actor))
        except Exception as e:  # 命令失败折成终态回执,不逃逸到队列循环
            log.exception("命令执行失败: %s", spec.action)
            return CommandOutcome(status="FAILED", error=str(e)[:500])

    async def process(
        self, envelope: CommandEnvelope, *, source: str = "control"
    ) -> CommandOutcome:
        """处理一条队列命令(claim 之后);参数校验失败也返回终态供 ack。"""
        if not envelope.idempotency_key or not envelope.command_id:
            return CommandOutcome(status="REJECTED", error="命令缺 command_id/idempotency_key")
        params = cast("object", envelope.params)
        if not isinstance(params, dict):
            # wire 上的 params 可能不是对象;必须拒绝并回执,不能让循环异常退出
            return CommandOutcome(status="REJECTED", error="命令 params 必须为对象")
        return await self.execute(
            command_id=envelope.command_id,
            idempotency_key=envelope.idempotency_key,
            action=envelope.action,
            action_version=envelope.action_version,
            params=envelope.params,
            actor=envelope.actor or "control-plane",
            source=source,
            reason=envelope.reason,
        )
