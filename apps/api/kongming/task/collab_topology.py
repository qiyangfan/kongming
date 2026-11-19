"""协作拓扑配置面:步骤声明协作拓扑(归一钳制落库)与执行期决策载荷组装。

绑定口径与 Bot 运行时同源;解析失败的 Bot 如实降级为无绑定(不虚报人格注入)。
"""

from typing import Any, cast

from kongming.task import models as m
from kongming.task.errors import TaskConflict, TaskNotFound, TopologyBotNotFound
from kongming.task.ports import CollabBotResolver, TaskCollabRepo
from kongming.task.service_common import now_iso


class TaskTopologyService:
    def __init__(
        self,
        repo: TaskCollabRepo,
        *,
        bot_resolver: CollabBotResolver | None = None,
    ) -> None:
        self._repo = repo
        self._bot_resolver = bot_resolver

    async def set_step_topology(
        self,
        *,
        task_id: str,
        step_id: str,
        tenant_id: str,
        user_id: str,
        raw_topology: dict[str, Any],
        max_workers_cap: int,
    ) -> dict[str, object]:
        """步骤声明协作拓扑(配置面):归属校验 → 步骤 PENDING 闸 → Bot 存在性
        校验(绑定口径同 Bot 运行时)→ 归一钳制落库。返回归一后的拓扑。"""
        task = await self._repo.get_task(task_id=task_id, tenant_id=tenant_id, user_id=user_id)
        if task is None:
            raise TaskNotFound
        step = await self._repo.get_step(task_id=task_id, step_id=step_id)
        if step is None:
            raise TaskNotFound
        if str(step["status"]) != m.S_PENDING:
            raise TaskConflict("仅 PENDING 步骤可声明协作拓扑")
        from kongming.config import get_settings

        topo = m.normalize_collab_topology(
            raw_topology,
            max_workers_cap=max_workers_cap,
            max_rounds_cap=get_settings().collab_max_rounds,
        )
        roster = m.decomposition_roster(topo)
        self._assert_binding_shape(topo=topo, roster=roster, raw=raw_topology)
        await self._assert_bots_resolvable(tenant_id=tenant_id, topo=topo, roster=roster)
        if not await self._repo.update_step_topology(
            task_id=task_id, step_id=step_id, topology=topo
        ):
            raise TaskConflict("步骤状态已变更,不可声明协作拓扑")
        await self._repo.append_task_event(
            task_id=task_id,
            # 显式写回事件类型，避免拓扑对象中的 kind 覆盖信封字段。
            event={"at": now_iso(), "step": step_id, **topo, "kind": "collab_topology_set"},
        )
        return topo

    @staticmethod
    def _assert_binding_shape(
        *,
        topo: dict[str, object],
        roster: list[dict[str, object]],
        raw: dict[str, Any],
    ) -> None:
        """绑定形态一致性:顶层 bot_key 与圆桌/分工名册不共存(歧义声明拒绝)。
        判原始载荷:归一结果在圆桌分支已剥 bot_key,判归一值恒假(死闸)。"""
        if str(topo.get("kind") or "") == m.COLLAB_KIND_ROUNDTABLE and raw.get("bot_key"):
            # 圆桌绑定收口在名册(逐参与者);顶层 bot_key 不生效
            raise TaskConflict("圆桌形态不受理顶层 bot_key(用参与者名册绑定)")
        if roster and raw.get("bot_key"):
            # 分工名册绑定收口在名册(逐成员),顶层单绑定与名册不共存
            raise TaskConflict("分工名册与顶层 bot_key 不共存(用名册逐成员绑定)")

    async def _assert_bots_resolvable(
        self,
        *,
        tenant_id: str,
        topo: dict[str, object],
        roster: list[dict[str, object]],
    ) -> None:
        """拓扑声明的 Bot 须全部可解析;解析器未接线时任何绑定都视为不可用。"""
        if self._bot_resolver is None:
            participants = cast("list[dict[str, object]]", topo.get("participants") or [])
            any_bound = (
                topo.get("bot_key")
                or any(p.get("bot_key") for p in participants)
                or any(w.get("bot_key") for w in roster)
            )
            if any_bound:
                raise TaskConflict("协作 Bot 绑定未启用")
            return
        if str(topo.get("kind") or "") == m.COLLAB_KIND_ROUNDTABLE:
            for p in cast("list[dict[str, object]]", topo.get("participants") or []):
                await self._require_bot(tenant_id=tenant_id, bot_key=p.get("bot_key"))
            return
        for w in roster:
            await self._require_bot(tenant_id=tenant_id, bot_key=w.get("bot_key"))
        await self._require_bot(tenant_id=tenant_id, bot_key=topo.get("bot_key"))

    async def _require_bot(self, *, tenant_id: str, bot_key: object) -> None:
        """声明引用的 Bot 存在性校验(空 key 跳过);解析失败即拒绝声明。"""
        if not bot_key or self._bot_resolver is None:
            return
        if await self._bot_resolver.resolve(tenant_id=tenant_id, bot_key=str(bot_key)) is None:
            raise TopologyBotNotFound(str(bot_key))

    async def _resolve_bot(self, *, tenant_id: str, bot_key: object) -> dict[str, object] | None:
        """运行时解析单个 Bot;失败(含解析器未接线)如实返回 None。"""
        if not bot_key or self._bot_resolver is None:
            return None
        return await self._bot_resolver.resolve(tenant_id=tenant_id, bot_key=str(bot_key))

    async def collab_decision(self, *, tenant_id: str, step: dict[str, Any]) -> dict[str, object]:
        """协作步骤的执行决策载荷:拓扑(以库内归一值为准,再按当前
        部署上限复钳)+ Bot 运行时解析(人格/工具子集;解析失败如实降级为
        无绑定,不虚报人格注入)。圆桌形态逐参与者解析各自 Bot。"""
        from kongming.config import get_settings

        topo = m.normalize_collab_topology(
            cast("dict[str, Any] | None", step.get("collab_topology")),
            max_workers_cap=get_settings().collab_max_workers,
            max_rounds_cap=get_settings().collab_max_rounds,
        )
        if str(topo.get("kind") or "") == m.COLLAB_KIND_ROUNDTABLE:
            parts: list[dict[str, object]] = []
            for p in cast("list[dict[str, object]]", topo.get("participants") or []):
                bot = await self._resolve_bot(tenant_id=tenant_id, bot_key=p.get("bot_key"))
                parts.append({**p, "bot": bot})
            return {**topo, "participants": parts}
        bot = await self._resolve_bot(tenant_id=tenant_id, bot_key=topo.get("bot_key"))
        out: dict[str, object] = {**topo, "bot": bot}
        # 分工名册逐成员运行时解析:失败如实降级
        # None 不虚报人格注入(剔除在执行面 fail-closed 收口,圆桌同口径)
        roster = m.decomposition_roster(topo)
        if roster:
            workers: list[dict[str, object]] = []
            for w in roster:
                w_bot = await self._resolve_bot(tenant_id=tenant_id, bot_key=w.get("bot_key"))
                workers.append({**w, "bot": w_bot})
            out["workers"] = workers
        return out
