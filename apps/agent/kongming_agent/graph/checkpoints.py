"""LangGraph 检查点经 api 内部端点读写,agent 不直连数据库;控制面只存
不透明数据,序列化由 agent 负责。

写入失败继续执行但不保留图级快照;读取失败或无记录返回 `None`,由调用方重跑步骤。"""

import base64
import contextvars
import logging
from collections.abc import Sequence
from typing import Any, cast

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.types import RunnableConfig  # type: ignore[attr-defined]

from kongming_agent import deps

log = logging.getLogger(__name__)

# 任务通道执行上下文(每步执行前由组合根设置;适配器回调据此携带租户与归属)
CHECKPOINT_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "checkpoint_context", default=None
)


def set_checkpoint_context(*, tenant_key: str, task_id: str, step_id: str, trace_id: str) -> None:
    CHECKPOINT_CONTEXT.set(
        {
            "tenant_key": tenant_key,
            "task_id": task_id,
            "step_id": step_id,
            "trace_id": trace_id,
        }
    )


def reset_checkpoint_context() -> None:
    CHECKPOINT_CONTEXT.set(None)


def thread_id_for_task(task_id: str, step_id: str) -> str:
    """线程确定性派生:api 不感知图语义,恢复时 agent 自查快照决定
    续跑或整步回退;派发-恢复双方向用同一派生,天然对齐。"""
    return f"task:{task_id}:step:{step_id}"


def _ctx() -> dict[str, str]:
    ctx = CHECKPOINT_CONTEXT.get()
    if ctx is None:
        raise RuntimeError("检查点上下文未设置(任务通道组合根负责 set)")
    return ctx


class InternalCheckpointSaver(BaseCheckpointSaver):  # type: ignore[type-arg]
    """控制面回调检查点保存器(异步协议;幂等/降级语义见模块 docstring)。"""

    def _mark_degraded(self, op: str, err: object) -> None:
        ctx = CHECKPOINT_CONTEXT.get()
        if isinstance(ctx, dict):
            ctx["degraded"] = "1"
        log.warning("检查点%s失败(降级): %s", op, err)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        cfg = config.get("configurable") or {}
        thread_id = str(cfg.get("thread_id") or "")
        ns = str(cfg.get("checkpoint_ns") or "")
        if not thread_id:
            return None
        ctx = _ctx()
        latest = await deps.get_api().graph_checkpoint_latest(
            tenant_key=ctx["tenant_key"], thread_id=thread_id, checkpoint_ns=ns
        )
        if latest is None:
            return None  # 缺席/失败同形:回退整步重跑(降级不谎称)
        try:
            checkpoint, metadata, cp_id = self._decode_tuple(latest)
            writes = await self._load_writes(ctx["tenant_key"], thread_id, ns, cp_id)
        except Exception as e:  # noqa: BLE001
            self._mark_degraded("读取", e)
            return None
        tup_config: RunnableConfig = {
            "configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cp_id}
        }
        return CheckpointTuple(
            config=tup_config,
            checkpoint=checkpoint,
            metadata=cast_metadata(metadata),
            pending_writes=writes or None,
        )  # pyright: ignore[reportUnknownArgumentType]

    def _decode_tuple(self, latest: dict[str, Any]) -> tuple[Checkpoint, dict[str, Any], str]:
        """解包 checkpoint 信封,返回 (检查点, 元数据, 检查点 id)。"""
        envelope = cast(
            "dict[str, Any]",
            self.serde.loads_typed(  # pyright: ignore[reportUnknownMemberType]
                (str(latest["payload_type"]), base64.b64decode(str(latest["payload_b64"])))
            ),
        )
        checkpoint = cast("Checkpoint", envelope["checkpoint"])
        metadata = cast("dict[str, Any]", envelope.get("metadata") or {})
        return checkpoint, metadata, str(latest["checkpoint_id"] or checkpoint["id"])

    async def _load_writes(
        self, tenant_key: str, thread_id: str, ns: str, cp_id: str
    ) -> list[tuple[str, str, Any]]:
        """拉取检查点待写通道并解包(失败由调用方按降级处理)。"""
        rows = await deps.get_api().graph_checkpoint_writes(
            tenant_key=tenant_key,
            thread_id=thread_id,
            checkpoint_ns=ns,
            checkpoint_id=cp_id,
        )
        writes: list[tuple[str, str, Any]] = []
        for r in rows or []:
            channel, value = self.serde.loads_typed(  # pyright: ignore[reportUnknownMemberType]
                (str(r["payload_type"]), base64.b64decode(str(r["payload_b64"])))
            )
            writes.append((str(r["write_task_id"] or ""), str(channel), value))
        return writes

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any] | None,
    ) -> RunnableConfig:
        cfg = config.get("configurable") or {}
        thread_id = str(cfg.get("thread_id") or "")
        ns = str(cfg.get("checkpoint_ns") or "")
        ctx = _ctx()
        payload_type, payload = self.serde.dumps_typed(  # pyright: ignore[reportUnknownMemberType]
            {"checkpoint": checkpoint, "metadata": metadata}
        )
        ok = await deps.get_api().graph_checkpoint_put(
            tenant_key=ctx["tenant_key"],
            thread_id=thread_id,
            checkpoint_ns=ns,
            kind="checkpoint",
            checkpoint_id=str(checkpoint["id"]),
            task_id=ctx["task_id"],
            step_id=ctx["step_id"],
            payload_type=payload_type,
            payload=payload,
        )
        if not ok:
            self._mark_degraded("写入", "控制面拒绝/不可达")
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": str(checkpoint["id"]),
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        cfg = config.get("configurable") or {}
        thread_id = str(cfg.get("thread_id") or "")
        ns = str(cfg.get("checkpoint_ns") or "")
        cp_id = str(cfg.get("checkpoint_id") or "")
        ctx = _ctx()
        for channel, value in writes:
            payload_type, payload = self.serde.dumps_typed(  # pyright: ignore[reportUnknownMemberType]
                [channel, value]
            )
            ok = await deps.get_api().graph_checkpoint_put(
                tenant_key=ctx["tenant_key"],
                thread_id=thread_id,
                checkpoint_ns=ns,
                kind="write",
                checkpoint_id=cp_id,
                task_id=ctx["task_id"],
                step_id=ctx["step_id"],
                payload_type=payload_type,
                payload=payload,
            )
            if not ok:
                self._mark_degraded("部分写入", f"task={task_id} channel={channel}")
                return

    async def adelete_thread(self, thread_id: str) -> None:
        ctx = _ctx()
        ok = await deps.get_api().graph_checkpoint_delete_thread(
            tenant_key=ctx["tenant_key"], thread_id=thread_id
        )
        if not ok:
            self._mark_degraded("清理", thread_id)


def cast_metadata(metadata: object) -> CheckpointMetadata:
    """控制面往返的 metadata 为普通 dict;按图框架口径收窄(缺字段补默认)。"""
    d: dict[str, Any] = cast("dict[str, Any]", metadata) if isinstance(metadata, dict) else {}
    return cast(
        "CheckpointMetadata",
        {
            "source": str(d.get("source") or "loop"),
            "step": int(d.get("step") or 0),
            "writes": d.get("writes") if isinstance(d.get("writes"), dict) else None,
        },
    )
