"""workflow 路由(薄适配层):/system/workflow/* 端点族。

定义管理挂 system:workflow:manage;运行挂 workflow:execute(节点级防御
以触发者身份逐层复校——无权限放大);租户解析与 chat 同构(跨域装配
先例:toolcall/bot router)。组合根:sql=internal execute 适配(异常
翻译与 ExecuteRunner 同范式);http=HttpNodeRunner;agent=回调
(工具集=触发者权限点过滤,channel="workflow")。
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo, SqlPermRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.agent_client import AgentClient
from kongming.chat.repository import ChatRepository
from kongming.chat.router import get_agent_client
from kongming.db import get_session
from kongming.internal import service as internal_service
from kongming.internal.service import InvalidDatasource, TenantBindingForbidden, TenantNotFound
from kongming.toolcall.registry import filter_specs
from kongming.workflow.http_runner import HttpNodeRunner
from kongming.workflow.repository import SqlWorkflowRepo
from kongming.workflow.service import (
    WorkflowConflict,
    WorkflowDisabled,
    WorkflowForbidden,
    WorkflowNotFound,
    WorkflowService,
    WorkflowValidationError,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/workflow", tags=["workflow"])

MANAGE = "system:workflow:manage"
EXECUTE = "workflow:execute"


async def _member_roles(session: AsyncSession, user_id: str) -> dict[str, str]:
    """用户团队角色映射(team_id→role,可见性判定用)。"""
    from kongming.team.repository import SqlTeamRepo

    return await SqlTeamRepo(session).role_map(user_id=user_id)


# ── runner 适配(组合根) ──


class InternalSqlRunner:
    """绑定 internal SQL 执行服务，并沿用触发者身份校验。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def run_sql(
        self,
        *,
        sql: str,
        datasource_key: str,
        trace_id: str,
        tenant_key: str,
        user_id: str,
    ) -> dict[str, Any]:
        try:
            r = await internal_service.execute(
                self._s,
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=sql,
                user_id=user_id,
                question=None,
                limit=1000,
            )
        except (InvalidDatasource, TenantNotFound, TenantBindingForbidden) as e:
            # 竞态/非法源收敛(与 ExecuteRunner 同范式)
            return {"verdict": "BLOCKED", "reason": str(e), "columns": [], "rows": []}
        return {
            "verdict": r.verdict,
            "reason": r.reason,
            "columns": r.columns,
            "rows": r.rows,
        }


class AgentWorkflowRunner:
    """AgentRunner 绑定:ReAct 回调;工具=触发者权限点过滤集。"""

    def __init__(self, session: AsyncSession, agent: AgentClient) -> None:
        self._s = session
        self._agent = agent

    async def run_agent(
        self,
        *,
        instruction: str,
        datasource_key: str,
        trace_id: str,
        tenant_key: str,
        user_id: str,
    ) -> dict[str, Any]:
        perms = await SqlPermRepo(self._s).list_codes(user_id)
        tools = [dict(spec) for spec in filter_specs(perms)]
        try:
            reply = await self._agent.query_react(
                trace_id=trace_id,
                tenant_key=tenant_key,
                question=instruction,
                user_id=user_id,
                history=[],
                datasource_key=datasource_key,
                tools=tools,
                bot_system_prompt="",
                model=None,
                channel="workflow",  # 审计溯源
            )
        except Exception as e:  # noqa: BLE001  # 节点级收敛(fail-fast 由引擎定)
            return {"success": False, "error": str(e)[:200]}
        return {
            "success": reply.success,
            "summary": reply.summary,
            "sql": reply.sql,
            "columns": reply.columns,
            "rows": reply.rows,
            "error": reply.error,
        }


class _WorkflowOperLogger:
    def __init__(self, session: AsyncSession, actor_id: str) -> None:
        self._repo = SqlOperLogRepo(session)
        self._actor = actor_id

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
        await self._repo.add(self._actor, action, target, detail)


def get_workflow_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
    user: Annotated[dict[str, object], Depends(current_user)],
) -> WorkflowService:
    return WorkflowService(
        repo=SqlWorkflowRepo(session),
        sql=InternalSqlRunner(session),
        http=HttpNodeRunner(),
        agent=AgentWorkflowRunner(session, agent),
        oper=_WorkflowOperLogger(session, str(user["sub"])),
    )


async def _tenant_or_403(
    user: dict[str, object], session: AsyncSession, header: str | None
) -> tuple[str, str]:
    """与 chat 同构:显式 X-Tenant-Id 白名单/缺省默认租户;返回 (id, key)。"""
    repo = ChatRepository(session)
    uid = str(user["sub"])
    if header is not None:
        tid = await repo.find_bound_tenant(user_id=uid, tenant_key=header)
        if tid is None:
            raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
        return str(tid), header
    default = await repo.find_default_tenant(user_id=uid)
    if default is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
    return str(default[0]), str(default[1])


# ── 传输层模型 ──


class WorkflowSaveIn(BaseModel):
    wf_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    definition: dict[str, Any]


class WorkflowRunIn(BaseModel):
    inputs: dict[str, Any] = {}
    version: int | None = None


class WorkflowDefOut(BaseModel):
    """工作流定义条目;definition 为自由 JSON。"""

    id: str
    wf_key: str
    name: str
    version: int
    enabled: bool
    definition: dict[str, Any]
    created_at: str | None = None


class WorkflowVersionBriefOut(BaseModel):
    version: int
    enabled: bool
    created_at: str | None = None


class WorkflowDetailOut(BaseModel):
    latest: WorkflowDefOut
    versions: list[WorkflowVersionBriefOut]


class WorkflowRunItemOut(BaseModel):
    id: str
    version: int
    trigger_source: str
    status: str
    error: str | None = None
    duration_ms: int
    node_runs: list[dict[str, Any]]
    inputs: dict[str, Any]
    created_at: str | None = None


class WorkflowRunsPageOut(BaseModel):
    total: int
    items: list[WorkflowRunItemOut]


def _def_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "wf_key": row["wf_key"],
        "name": row["name"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "definition": row["definition"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


# ── 端点 ──


@router.post("", status_code=201, dependencies=[Depends(require_perm(MANAGE))])
async def save_workflow(
    body: WorkflowSaveIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant_id, _ = await _tenant_or_403(user, session, x_tenant_id)
    try:
        out = await svc.save_definition(
            tenant_id=tenant_id,
            wf_key=body.wf_key,
            name=body.name,
            definition=body.definition,
            created_by=str(user["sub"]),
            user_id=str(user["sub"]),
            member_roles=await _member_roles(session, str(user["sub"])),
        )
        await svc.commit()
    except WorkflowValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except WorkflowConflict:
        raise HTTPException(status_code=409, detail="WORKFLOW_CONFLICT") from None
    except WorkflowForbidden:
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    return out


@router.get("", response_model=list[WorkflowDefOut], dependencies=[Depends(require_perm(MANAGE))])
async def list_workflows(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[dict[str, Any]]:
    tenant_id, _ = await _tenant_or_403(user, session, x_tenant_id)
    roles = await _member_roles(session, str(user["sub"]))
    return [
        _def_out(r)
        for r in await svc.list_workflows(
            tenant_id=tenant_id, user_id=str(user["sub"]), team_ids=list(roles.keys())
        )
    ]


@router.get(
    "/{wf_key}", response_model=WorkflowDetailOut, dependencies=[Depends(require_perm(MANAGE))]
)
async def get_workflow(
    wf_key: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant_id, _ = await _tenant_or_403(user, session, x_tenant_id)
    try:
        out = await svc.get_workflow(
            tenant_id=tenant_id,
            wf_key=wf_key,
            user_id=str(user["sub"]),
            member_roles=await _member_roles(session, str(user["sub"])),
        )
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {
        "latest": _def_out(out["latest"]),
        "versions": [
            {
                "version": v["version"],
                "enabled": bool(v["enabled"]),
                "created_at": v["created_at"].isoformat() if v.get("created_at") else None,
            }
            for v in out["versions"]
        ],
    }


@router.post("/{wf_key}/enable", dependencies=[Depends(require_perm(MANAGE))])
async def enable_workflow(
    wf_key: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id, _ = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.set_enabled(
            tenant_id=tenant_id,
            wf_key=wf_key,
            enabled=True,
            user_id=str(user["sub"]),
            member_roles=await _member_roles(session, str(user["sub"])),
        )
        await svc.commit()
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except WorkflowForbidden:
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    return {"ok": True}


@router.post("/{wf_key}/disable", dependencies=[Depends(require_perm(MANAGE))])
async def disable_workflow(
    wf_key: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id, _ = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.set_enabled(
            tenant_id=tenant_id,
            wf_key=wf_key,
            enabled=False,
            user_id=str(user["sub"]),
            member_roles=await _member_roles(session, str(user["sub"])),
        )
        await svc.commit()
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except WorkflowForbidden:
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    return {"ok": True}


@router.delete("/{wf_key}", dependencies=[Depends(require_perm(MANAGE))])
async def delete_workflow(
    wf_key: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id, _ = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.delete(
            tenant_id=tenant_id,
            wf_key=wf_key,
            user_id=str(user["sub"]),
            member_roles=await _member_roles(session, str(user["sub"])),
        )
        await svc.commit()
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except WorkflowForbidden:
        raise HTTPException(status_code=403, detail="TEAM_ROLE_FORBIDDEN") from None
    return {"ok": True}


@router.post("/{wf_key}/run", dependencies=[Depends(require_perm(EXECUTE))])
async def run_workflow(
    wf_key: str,
    body: WorkflowRunIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant_id, tenant_key = await _tenant_or_403(user, session, x_tenant_id)
    try:
        return await svc.run(
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            wf_key=wf_key,
            user_id=str(user["sub"]),
            inputs=body.inputs,
            version=body.version,
            trigger_source="MANUAL",  # v1 统一 JWT 通道;API 触发即外部系统持 JWT
            trace_id=uuid.uuid4().hex,
            member_roles=await _member_roles(session, str(user["sub"])),
        )
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except WorkflowDisabled:
        raise HTTPException(status_code=409, detail="WORKFLOW_DISABLED") from None


@router.get(
    "/{wf_key}/runs",
    response_model=WorkflowRunsPageOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def page_runs(
    wf_key: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    tenant_id, _ = await _tenant_or_403(user, session, x_tenant_id)
    try:
        total, rows = await svc.page_runs(
            tenant_id=tenant_id,
            wf_key=wf_key,
            page=page,
            size=size,
            user_id=str(user["sub"]),
            member_roles=await _member_roles(session, str(user["sub"])),
        )
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {
        "total": total,
        "items": [
            {
                "id": str(r["id"]),
                "version": r["version"],
                "trigger_source": r["trigger_source"],
                "status": r["status"],
                "error": r["error"],
                "duration_ms": r["duration_ms"],
                "node_runs": r["node_runs"],
                "inputs": r["inputs"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ],
    }


@router.get(
    "/{wf_key}/runs/{run_id}",
    response_model=WorkflowRunItemOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def get_run(
    wf_key: str,
    run_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[WorkflowService, Depends(get_workflow_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> WorkflowRunItemOut:
    """返回工作流运行详情；可见范围与分页列表一致。"""
    tenant_id, _ = await _tenant_or_403(user, session, x_tenant_id)
    try:
        r = await svc.get_run(
            tenant_id=tenant_id,
            wf_key=wf_key,
            run_id=str(run_id),
            user_id=str(user["sub"]),
            member_roles=await _member_roles(session, str(user["sub"])),
        )
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    if r is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return WorkflowRunItemOut(
        id=str(r["id"]),
        version=int(r["version"]),
        trigger_source=str(r["trigger_source"]),
        status=str(r["status"]),
        error=(str(r["error"]) if r.get("error") else None),
        duration_ms=int(r["duration_ms"]),
        node_runs=list(r["node_runs"] or []),
        inputs=dict(r["inputs"] or {}),
        created_at=(r["created_at"].isoformat() if r.get("created_at") else None),
    )
