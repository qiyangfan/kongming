"""workflow 用例编排:定义 CRUD(版本冻结)+ 运行(引擎调用+
run 落库)+ 历史分页。用例层不依赖 FastAPI 或 SQLAlchemy。"""

import re
from typing import Any, Protocol

from kongming.team.models import can_edit as _team_can_edit
from kongming.team.models import can_manage as _team_can_manage
from kongming.team.models import visible as _team_visible
from kongming.workflow import engine
from kongming.workflow.models import (
    DagValidationError,
    DefinitionConflictError,
    validate_dag,
)
from kongming.workflow.ports import AgentRunner, HttpRunner, SqlRunner, WorkflowRepo

WF_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class WorkflowValidationError(Exception):
    """领域层 400(key 形态/DAG 校验)。"""


class WorkflowNotFound(Exception):
    """不存在/跨租户同形。"""


class WorkflowDisabled(Exception):
    """禁用态触发。"""


class WorkflowForbidden(Exception):
    """可见但角色不足(VIEWER 编辑/MEMBER 删除)——403 语义。"""


class WorkflowConflict(Exception):
    """把同 key 的并发版本冲突映射为 409。"""


class WorkflowOperLogger(Protocol):
    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


class WorkflowService:
    def __init__(
        self,
        *,
        repo: WorkflowRepo,
        sql: SqlRunner,
        http: HttpRunner,
        agent: AgentRunner,
        oper: WorkflowOperLogger | None = None,
    ) -> None:
        self._repo = repo
        self._sql = sql
        self._http = http
        self._agent = agent
        self._oper = oper

    async def _audit(self, action: str, target: str, detail: dict[str, object]) -> None:
        if self._oper is not None:
            await self._oper.log(action=action, target=target, detail=detail)

    @staticmethod
    def _assert_visible(
        row: dict[str, Any], *, user_id: str, member_roles: dict[str, str] | None
    ) -> None:
        """可见性闸(user_id 空=管理面/调度内部调用,不拦)。"""
        if not user_id:
            return
        team_id = None if row.get("team_id") is None else str(row["team_id"])
        if not _team_visible(
            team_id=team_id,
            creator_id=str(row["created_by"]),
            user_id=user_id,
            member_role=(member_roles or {}).get(team_id or ""),
        ):
            raise WorkflowNotFound(str(row.get("wf_key") or row.get("id") or "?"))

    @staticmethod
    def _assert_editable(
        row: dict[str, Any], *, user_id: str, member_roles: dict[str, str] | None
    ) -> None:
        """编辑闸(新版本/启停):创建者或 MEMBER+。"""
        if not user_id:
            return
        team_id = None if row.get("team_id") is None else str(row["team_id"])
        if not _team_can_edit(
            team_id=team_id,
            creator_id=str(row["created_by"]),
            user_id=user_id,
            member_role=(member_roles or {}).get(team_id or ""),
        ):
            raise WorkflowForbidden(str(row.get("wf_key") or "?"))

    @staticmethod
    def _assert_manageable(
        row: dict[str, Any], *, user_id: str, member_roles: dict[str, str] | None
    ) -> None:
        """全权闸(删除):创建者或 OWNER。"""
        if not user_id:
            return
        team_id = None if row.get("team_id") is None else str(row["team_id"])
        if not _team_can_manage(
            team_id=team_id,
            creator_id=str(row["created_by"]),
            user_id=user_id,
            member_role=(member_roles or {}).get(team_id or ""),
        ):
            raise WorkflowForbidden(str(row.get("wf_key") or "?"))

    async def commit(self) -> None:
        await self._repo.commit()

    # ── 定义管理 ──

    async def save_definition(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        name: str,
        definition: dict[str, Any],
        created_by: str,
        user_id: str = "",
        member_roles: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """保存=新版本(版本冻结);保存即结构校验(spec)。
        编辑闸(对他人/非成员工作流存新版本=劫持,创建者或 MEMBER+
        才放行);新版本继承上一版 team_id(防团队工作流失守)。"""
        if not WF_KEY_RE.fullmatch(wf_key):
            raise WorkflowValidationError("wf_key 须为小写字母开头的字母/数字/下划线(2-64 位)")
        if not name.strip() or len(name) > 128:
            raise WorkflowValidationError("名称非空且 ≤128 字符")
        try:
            validate_dag(definition)
        except DagValidationError as e:
            raise WorkflowValidationError(str(e)) from e
        # 编辑闸(新版本=编辑语义;首版无闸)
        prev = await self._repo.latest_def(tenant_id=tenant_id, wf_key=wf_key)
        if prev is not None:
            self._assert_editable(prev, user_id=user_id, member_roles=member_roles)
        version = await self._repo.next_version(tenant_id=tenant_id, wf_key=wf_key)
        try:
            await self._repo.insert_def(
                tenant_id=tenant_id,
                wf_key=wf_key,
                name=name.strip(),
                version=version,
                definition=definition,
                created_by=created_by,
                # 继承上一版归属(防团队工作流失守;首版 None=个人)
                team_id=None if prev is None else prev.get("team_id"),
            )
        except DefinitionConflictError as e:
            raise WorkflowConflict(wf_key) from e
        await self._audit("workflow.save", wf_key, {"version": version, "name": name.strip()})
        return {"wf_key": wf_key, "version": version}

    async def list_workflows(
        self, *, tenant_id: str, user_id: str = "", team_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """归属过滤(user_id 非空时启用;空=管理面全量)。"""
        return await self._repo.list_latest(tenant_id=tenant_id, user_id=user_id, team_ids=team_ids)

    async def get_workflow(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        user_id: str = "",
        member_roles: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        latest = await self._repo.latest_def(tenant_id=tenant_id, wf_key=wf_key)
        if latest is None:
            raise WorkflowNotFound(wf_key)
        self._assert_visible(latest, user_id=user_id, member_roles=member_roles)
        versions = await self._repo.list_versions(tenant_id=tenant_id, wf_key=wf_key)
        return {"latest": latest, "versions": versions}

    async def set_enabled(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        enabled: bool,
        user_id: str = "",
        member_roles: dict[str, str] | None = None,
    ) -> None:
        latest = await self._repo.latest_def(tenant_id=tenant_id, wf_key=wf_key)
        if latest is None:
            raise WorkflowNotFound(wf_key)
        self._assert_editable(latest, user_id=user_id, member_roles=member_roles)
        await self._repo.set_enabled(def_id=str(latest["id"]), tenant_id=tenant_id, enabled=enabled)
        await self._audit(
            "workflow.enable" if enabled else "workflow.disable", wf_key, {"enabled": enabled}
        )

    async def delete(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        user_id: str = "",
        member_roles: dict[str, str] | None = None,
    ) -> None:
        latest = await self._repo.latest_def(tenant_id=tenant_id, wf_key=wf_key)
        if latest is None:
            raise WorkflowNotFound(wf_key)
        self._assert_manageable(latest, user_id=user_id, member_roles=member_roles)
        if not await self._repo.delete_key(tenant_id=tenant_id, wf_key=wf_key):
            raise WorkflowNotFound(wf_key)
        await self._audit("workflow.delete", wf_key, {})

    # ── 运行 ──

    async def run(
        self,
        *,
        tenant_id: str,
        tenant_key: str,
        wf_key: str,
        user_id: str,
        inputs: dict[str, Any],
        version: int | None,
        trigger_source: str,
        trace_id: str,
        member_roles: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """执行编排:定义解析(版本/启用)→ 引擎 → run 落库(终态全落)。"""
        row = await self._resolve_definition(
            tenant_id=tenant_id,
            wf_key=wf_key,
            version=version,
            user_id=user_id,
            member_roles=member_roles,
        )
        nodes = validate_dag(row["definition"])  # 落库前已验,此处深防御重验
        # 读取即提交释放连接(执行期 ≤120s 不占控制面连接槽;
        # 引擎 sql/agent 节点用同会话惰性重取连接,run 落库再入新事务)
        await self._repo.commit()
        outcome = await engine.run_dag(
            nodes,
            inputs,
            runners=(self._sql, self._http, self._agent),
            trace_id=trace_id,
            tenant_key=tenant_key,
            user_id=user_id,
        )
        node_runs = [
            {
                "node_id": nr.node_id,
                "status": nr.status,
                "outputs": nr.outputs,
                "error": nr.error,
                "duration_ms": nr.duration_ms,
            }
            for nr in outcome.node_runs
        ]
        run_id = await self._repo.insert_run(
            tenant_id=tenant_id,
            wf_key=wf_key,
            version=int(row["version"]),
            trigger_source=trigger_source,
            inputs=inputs,
            status=outcome.status,
            node_runs=node_runs,
            error=outcome.error,
            duration_ms=outcome.duration_ms,
        )
        await self._repo.commit()
        return {
            "status": outcome.status,
            "error": outcome.error,
            "duration_ms": outcome.duration_ms,
            "node_runs": node_runs,
            "version": int(row["version"]),
            "run_id": run_id,  # trigger 关联回填
        }

    async def _resolve_definition(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        version: int | None,
        user_id: str,
        member_roles: dict[str, str] | None,
    ) -> dict[str, Any]:
        """取目标版本定义并校验可见/启用(执行前统一前置)。"""
        if version is not None:
            row = await self._repo.get_def(tenant_id=tenant_id, wf_key=wf_key, version=version)
        else:
            row = await self._repo.latest_def(tenant_id=tenant_id, wf_key=wf_key)
        if row is None:
            raise WorkflowNotFound(wf_key)
        self._assert_visible(row, user_id=user_id, member_roles=member_roles)
        if not row["enabled"]:
            raise WorkflowDisabled(wf_key)
        return row

    async def page_runs(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        page: int,
        size: int,
        user_id: str = "",
        member_roles: dict[str, str] | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        # 历史可见性闸(与 get 同口径;user_id 空=管理面内部调用)
        latest = await self._repo.latest_def(tenant_id=tenant_id, wf_key=wf_key)
        if latest is not None:
            self._assert_visible(latest, user_id=user_id, member_roles=member_roles)
        # 删除定义后仍保留历史运行记录；查询条件负责隔离租户。
        return await self._repo.page_runs(tenant_id=tenant_id, wf_key=wf_key, page=page, size=size)

    async def get_run(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        run_id: str,
        user_id: str = "",
        member_roles: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """run 详情:完整快照含 outputs;可见性与分页列表同口径
        (def 存在则闸;def 删后历史仍可读——租户隔离由查询条件保证)。"""
        latest = await self._repo.latest_def(tenant_id=tenant_id, wf_key=wf_key)
        if latest is not None:
            self._assert_visible(latest, user_id=user_id, member_roles=member_roles)
        return await self._repo.get_run(tenant_id=tenant_id, wf_key=wf_key, run_id=run_id)
