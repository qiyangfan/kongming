"""report 路由:公开端点(生成/进度/详情/列表/编辑/刷新/删除/导出/版本)
+ 内部进度端点(agent 编排回写 phase)。

权限点:report:generate(发起/编辑/刷新/删除)、report:read(查询面)、
report:export(导出);归属收口=本人本租户,越权 404 同形。
生成派发=FastAPI 后台任务(任务自持会话,连接卫生与 chat/title_tasks 同款)。
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.agent_client import AgentClient
from kongming.chat.models import ChartSuggestion, MessageMeta
from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
from kongming.db import get_session, get_sessionmaker, uuid7
from kongming.internal.service import execute as defense_execute
from kongming.report.models import section_from_row
from kongming.report.repository import SqlReportRepo
from kongming.report.service import ReportConflict, ReportNotFound, ReportService
from kongming.trace import ensure_trace_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["report"])
internal_router = APIRouter(prefix="/internal/report", tags=["internal"])

# 会话上下文摘要轮数上限(入口 a:只读素材,防 prompt 无界)
_DIGEST_MAX_TURNS = 10


def _get_service(session: AsyncSession) -> ReportService:
    agent = AgentClient(get_settings())

    class _AgentGateway:
        async def generate_report(self, payload: dict[str, object]) -> dict[str, object] | None:
            return await agent.generate_report(payload)

    class _Runner:
        """刷新数据执行器:internal.service.execute 全量防御(与 rerun 同源);
        自持短会话(审计落库随用例提交),连接卫生与标题任务同款。"""

        async def run(
            self,
            *,
            trace_id: str,
            tenant_key: str,
            datasource_key: str,
            sql_text: str,
            user_id: str,
        ) -> tuple[str, list[str], list[list[object]], str | None]:
            async with get_sessionmaker()() as s:
                out = await defense_execute(
                    s,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                    datasource_key=datasource_key,
                    sql_text=sql_text,
                    user_id=user_id,
                    question=None,
                    # 上限 +1:保留「超限」判定能力(执行端截断后恒等长会丢标注)
                    limit=501,
                )
                await s.commit()
            return out.verdict, out.columns, out.rows, out.reason

    class _Cards:
        """报告卡写手:向发起会话追加 assistant 消息(result_json.report),
        artifacts 经 derive_artifacts 派生(与问答轮次同源单一定义)。"""

        async def append_report_card(
            self,
            *,
            session_id: str,
            tenant_id: str,
            report_id: str,
            version: int,
            status: str,
            title: str,
            summary: str,
            trace_id: str | None,
        ) -> None:
            from kongming.chat.models import derive_artifacts

            async with get_sessionmaker()() as s:
                card: dict[str, object] = {
                    "report_id": report_id,
                    "version": version,
                    "status": status,
                    "title": title,
                    "summary": summary,
                }
                artifacts = derive_artifacts(
                    sql="",
                    chart=ChartSuggestion(type="none"),
                    summary="",
                    datasource_key="",
                    report=card,
                )
                await ChatRepository(s).insert_message(
                    session_id=session_id,
                    role="ASSISTANT",
                    result_json=json.dumps(
                        {"report": card, "artifacts": artifacts}, ensure_ascii=False
                    ),
                    summary=summary,
                    status="SUCCESS",
                    meta=MessageMeta(trace_id=trace_id),
                )
                await s.commit()

    return ReportService(
        SqlReportRepo(session),
        _AgentGateway(),
        _Runner(),
        SqlOperLogRepo(session),
        _Cards(),
    )


async def _tenant_id(session: AsyncSession, user_id: str, tenant_key: str | None) -> str:
    """X-Tenant-Id 白名单复校(与 chat._resolve_tenant 同构)。"""
    from kongming.tenant import resolve_tenant

    resolved = await resolve_tenant(session, user_id=user_id, tenant_key=tenant_key)
    if resolved is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return resolved[0]


async def _tenant_key_of(session: AsyncSession, tenant_id: str) -> str:
    """租户 id → 权威 key:刷新取数按租户 schema 路由,与生成路径同源口径
    (不用原始 header 兜底——header 缺省时 "default" 会路由到错误租户)。"""
    from sqlalchemy import text as _text

    key = (
        await session.execute(
            _text("SELECT tenant_key FROM public.sys_tenant WHERE id = CAST(:t AS uuid)"),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return str(key)


# ── 请求/响应模型 ──


class GenerateIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    datasource_key: str = Field(default="default", max_length=64)
    session_id: str | None = None  # 入口 a:发起会话(产出 report 卡挂回)
    context_message_limit: int = Field(default=_DIGEST_MAX_TURNS, ge=1, le=50)
    title: str | None = Field(default=None, max_length=200)


class GenerateOut(BaseModel):
    report_id: str
    status: str
    phase: str


class ReportItem(BaseModel):
    id: str
    title: str
    status: str
    phase: str
    datasource_key: str
    version: int
    created_at: str
    updated_at: str


class ReportPageOut(BaseModel):
    items: list[ReportItem]
    total: int
    page: int
    size: int


class SectionOut(BaseModel):
    id: str
    parent_id: str | None
    title: str
    narrative_md: str
    bindings: list[dict[str, object]]
    chart: dict[str, object] | None
    ord: int
    data_status: str
    fail_reason: str | None


class VersionMetaOut(BaseModel):
    id: str
    version: int
    meta: dict[str, object]
    data_time_point: str
    created_at: str


class ReportDetailOut(BaseModel):
    report: ReportItem
    version_meta: VersionMetaOut | None
    sections: list[SectionOut]
    instruction: str = ""
    fail_reason: str | None = None


class ProgressOut(BaseModel):
    report_id: str
    status: str
    phase: str


class EditSectionIn(BaseModel):
    # 未知字段 422(编辑面只开放标题/叙述,防绑定/图表经编辑口漂移)
    model_config = ConfigDict(extra="forbid")

    section_id: str
    title: str | None = Field(default=None, max_length=200)
    narrative_md: str | None = None


class EditIn(BaseModel):
    base_version: int
    # 允许空清单(纯标题改名也产生新版本);至少传 title 或一个 section
    sections: list[EditSectionIn] = Field(default_factory=list[EditSectionIn])
    title: str | None = Field(default=None, max_length=200)


class MutateOut(BaseModel):
    report_id: str
    version: int
    status: str


def _item(r: dict[str, Any]) -> ReportItem:
    created = r["created_at"]
    updated = r["updated_at"]
    return ReportItem(
        id=str(r["id"]),
        title=str(r["title"]),
        status=str(r["status"]),
        phase=str(r["phase"]),
        datasource_key=str(r["datasource_key"]),
        version=int(str(r["version"])),
        created_at=created.isoformat() if isinstance(created, datetime) else str(created),
        updated_at=updated.isoformat() if isinstance(updated, datetime) else str(updated),
    )


def _section_out(r: dict[str, Any]) -> SectionOut:
    node = section_from_row(r)
    return SectionOut(
        id=node.section_id or "",
        parent_id=node.parent_id,
        title=node.title,
        narrative_md=node.narrative_md,
        bindings=[
            {
                "sql": b.sql,
                "status": b.status,
                "columns": b.columns,
                "rows": b.rows,
                "row_count": b.row_count,
                "truncated": b.truncated,
                "fail_reason": b.fail_reason,
            }
            for b in node.bindings
        ],
        chart=node.chart,
        ord=node.ord,
        data_status=node.data_status,
        fail_reason=node.fail_reason,
    )


def _detail_out(detail: dict[str, Any]) -> ReportDetailOut:
    sections_rows = cast("list[dict[str, Any]]", detail["sections"])
    report = detail["report"]
    vmeta = detail["version_meta"]
    vm: VersionMetaOut | None = None
    if vmeta is not None:
        dtp = vmeta["data_time_point"]
        vm = VersionMetaOut(
            id=str(vmeta["id"]),
            version=int(str(vmeta["version"])),
            meta=dict(cast("dict[str, object]", vmeta.get("meta") or {})),
            data_time_point=dtp.isoformat() if isinstance(dtp, datetime) else str(dtp),
            created_at=(
                vmeta["created_at"].isoformat()
                if isinstance(vmeta["created_at"], datetime)
                else str(vmeta["created_at"])
            ),
        )
    return ReportDetailOut(
        report=_item(report),
        version_meta=vm,
        sections=[_section_out(r) for r in sections_rows],
        instruction=str(report.get("instruction") or ""),
        fail_reason=cast("str | None", report.get("fail_reason")),
    )


# ── 生成发起(入口 a/b 同源;异步过程,发起后立即可查进度)──


@router.post(
    "",
    status_code=202,
    response_model=GenerateOut,
    dependencies=[Depends(require_perm("report:generate"))],
)
async def generate_report(
    body: GenerateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> GenerateOut:
    uid = str(user["sub"])
    tid = await _tenant_id(session, uid, x_tenant_id)
    trace_id = ensure_trace_id()
    context_digest = ""
    if body.session_id:
        digest = await _session_digest(
            session,
            session_id=body.session_id,
            user_id=uid,
            tenant_id=tid,
            limit=body.context_message_limit,
        )
        if digest is None:
            # 会话不存在/越权:404 同形,不发起生成(归属但空会话照常生成)
            raise HTTPException(status_code=404, detail="NOT_FOUND") from None
        context_digest = digest
    report_id = str(uuid7())
    svc = _get_service(session)
    await svc.create_generation(
        report_id=report_id,
        tenant_id=tid,
        user_id=uid,
        title=body.title or body.instruction[:80],
        instruction=body.instruction,
        datasource_key=body.datasource_key,
        session_id=body.session_id,
        trace_id=trace_id,
    )
    await session.commit()
    _schedule_generation(report_id=report_id, context_digest=context_digest)
    return GenerateOut(report_id=report_id, status="GENERATING", phase="PLANNING")


async def _session_digest(
    session: AsyncSession, *, session_id: str, user_id: str, tenant_id: str, limit: int
) -> str | None:
    """入口 a:会话既有问答产物 → 只读素材摘要(问/SQL/结论;不回写会话)。
    会话不存在/越权(非本人本租户)返回 None(调用方 404 同形);
    归属但无可用产物返回空串(以指令单独生成,不拒绝)。"""
    repo = ChatRepository(session)
    if (
        await repo.find_session_owned(session_id=session_id, user_id=user_id, tenant_id=tenant_id)
        is None
    ):
        return None
    lines: list[str] = []
    last_question = ""
    # 有界读取:素材只需最近 N 轮,防长会话大 result_json 全量拉取。
    # role 必须大写以匹配 CHECK；按轮次配对 USER 问题与 ASSISTANT 的 SQL 和结论。
    for msg in await repo.list_messages(session_id=session_id, limit=limit * 2):
        role = str(msg.get("role") or "")
        if role == "USER":
            last_question = str(msg.get("question") or "")
            continue
        if role != "ASSISTANT" or msg.get("status") != "SUCCESS":
            continue
        sql = str(msg.get("sql_text") or "")
        summary = str(msg.get("summary") or "")
        if not (last_question or sql or summary):
            continue
        lines.append(f"[轮次] 问:{last_question}")
        if sql:
            lines.append(f"SQL:\n{sql}")
        if summary:
            lines.append(f"结论:{summary}")
        if len(lines) >= limit * 3:
            break
    return "\n".join(lines[: limit * 3])


# 后台任务强引用集(防 GC:CPython 事件循环对 task 仅持弱引用)
_GEN_TASKS: set[asyncio.Task[None]] = set()

_REPORT_ROW_SQL = text(
    "SELECT r.id, r.tenant_id, r.user_id, r.session_id, r.title, r.status, r.phase,"
    "       r.datasource_key, r.version, r.instruction, r.trace_id, tn.tenant_key"
    " FROM public.report r"
    " JOIN public.sys_tenant tn ON tn.id = r.tenant_id"
    " WHERE r.id = CAST(:i AS uuid)"
)


def _schedule_generation(*, report_id: str, context_digest: str) -> None:
    """后台派发:任务自持会话(池占用与 LLM 调用解耦,标题任务同款);
    租户键以报告行归属为准(header 缺省/他人租户视图不污染回调解析);
    任何异常收敛 FAILED 终态(不静默停在 GENERATING)。"""

    async def _run() -> None:
        async with get_sessionmaker()() as session:
            row = (
                (await session.execute(_REPORT_ROW_SQL, {"i": report_id})).mappings().one_or_none()
            )
            await session.commit()  # 读后即放连接:agent 编排期间(≤600s)不占池
            if row is None:
                return
            svc = _get_service(session)
            try:
                card = await svc.run_generation(
                    report=dict(row),
                    tenant_key=str(row["tenant_key"] or "default"),
                    context_digest=context_digest,
                )
                await session.commit()  # 终态先落库;卡在提交后发送(不得先于终态可见)
                if card:
                    await svc.emit_card(card)
            except Exception:
                log.exception("报告生成后台任务失败: report_id=%s", report_id)
                await session.rollback()
                try:
                    await SqlReportRepo(session).finish_generation(
                        report_id=report_id,
                        status="FAILED",
                        fail_reason="生成编排异常(见服务端日志)",
                        usage_summary=None,
                        version=None,
                        version_meta=None,
                        data_time_point=datetime.now(UTC),
                        created_by=str(row["user_id"]),
                        sections=[],
                    )
                    await session.commit()
                except Exception:
                    log.exception("报告失败终态落库失败: report_id=%s", report_id)

    task = asyncio.get_running_loop().create_task(_run())
    _GEN_TASKS.add(task)
    task.add_done_callback(_GEN_TASKS.discard)


# ── 查询面 ──


@router.get("", response_model=ReportPageOut, dependencies=[Depends(require_perm("report:read"))])
async def list_reports(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    page: int = 1,
    size: int = 20,
) -> ReportPageOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    svc = _get_service(session)
    p, s = max(1, page), min(100, max(1, size))
    items, total = await svc.list_reports(tenant_id=tid, user_id=str(user["sub"]), page=p, size=s)
    return ReportPageOut(items=[_item(r) for r in items], total=total, page=p, size=s)


@router.get(
    "/{report_id}",
    response_model=ReportDetailOut,
    dependencies=[Depends(require_perm("report:read"))],
)
async def get_report(
    report_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    version: int | None = None,
) -> ReportDetailOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    svc = _get_service(session)
    try:
        detail = await svc.get_detail(
            report_id=report_id, tenant_id=tid, user_id=str(user["sub"]), version=version
        )
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return _detail_out(detail)


@router.get(
    "/{report_id}/progress",
    response_model=ProgressOut,
    dependencies=[Depends(require_perm("report:read"))],
)
async def get_progress(
    report_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ProgressOut:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    svc = _get_service(session)
    try:
        # 轻量读:只取报告行状态,不加载版本章节全文(轮询高频路径)
        p = await svc.get_progress(report_id=report_id, tenant_id=tid, user_id=str(user["sub"]))
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return ProgressOut(report_id=report_id, status=p["status"], phase=p["phase"])


@router.get("/{report_id}/versions", dependencies=[Depends(require_perm("report:read"))])
async def list_versions(
    report_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[dict[str, object]]:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    svc = _get_service(session)
    try:
        versions = await svc.list_versions(
            report_id=report_id, tenant_id=tid, user_id=str(user["sub"])
        )
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return [
        {
            "id": str(v["id"]),
            "version": int(str(v["version"])),
            "meta": dict(v.get("meta") or {}),
            "data_time_point": (
                v["data_time_point"].isoformat()
                if isinstance(v["data_time_point"], datetime)
                else str(v["data_time_point"])
            ),
            "created_at": (
                v["created_at"].isoformat()
                if isinstance(v["created_at"], datetime)
                else str(v["created_at"])
            ),
        }
        for v in versions
    ]


# ── 编辑/刷新/删除 ──


@router.put(
    "/{report_id}",
    response_model=MutateOut,
    dependencies=[Depends(require_perm("report:generate"))],
)
async def edit_report(
    report_id: str,
    body: EditIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> MutateOut:
    uid = str(user["sub"])
    tid = await _tenant_id(session, uid, x_tenant_id)
    svc = _get_service(session)
    try:
        new_version = await svc.save_edit(
            report_id=report_id,
            tenant_id=tid,
            user_id=uid,
            base_version=body.base_version,
            section_edits=[s.model_dump() for s in body.sections],
            title=body.title,
        )
        await session.commit()
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except ReportConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    detail = await svc.get_detail(report_id=report_id, tenant_id=tid, user_id=uid)
    return MutateOut(
        report_id=report_id, version=new_version, status=str(detail["report"]["status"])
    )


@router.post(
    "/{report_id}/refresh",
    response_model=MutateOut,
    dependencies=[Depends(require_perm("report:generate"))],
)
async def refresh_report(
    report_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> MutateOut:
    uid = str(user["sub"])
    tid = await _tenant_id(session, uid, x_tenant_id)
    svc = _get_service(session)
    trace_id = ensure_trace_id()
    try:
        new_version = await svc.refresh_data(
            report_id=report_id,
            tenant_id=tid,
            tenant_key=await _tenant_key_of(session, tid),
            user_id=uid,
            trace_id=trace_id,
        )
        await session.commit()
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except ReportConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    detail = await svc.get_detail(report_id=report_id, tenant_id=tid, user_id=uid)
    return MutateOut(
        report_id=report_id, version=new_version, status=str(detail["report"]["status"])
    )


@router.delete(
    "/{report_id}",
    status_code=204,
    dependencies=[Depends(require_perm("report:generate"))],
)
async def delete_report(
    report_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    svc = _get_service(session)
    try:
        await svc.delete(report_id=report_id, tenant_id=tid, user_id=str(user["sub"]))
        await session.commit()
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


# ── 导出 ──


@router.get(
    "/{report_id}/export",
    dependencies=[Depends(require_perm("report:export"))],
)
async def export_report(
    report_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    version: int | None = None,
) -> Response:
    tid = await _tenant_id(session, str(user["sub"]), x_tenant_id)
    svc = _get_service(session)
    try:
        md = await svc.export(
            report_id=report_id, tenant_id=tid, user_id=str(user["sub"]), version=version
        )
        await session.commit()  # 导出审计落库
    except ReportNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except ReportConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report-{report_id}.md"'},
    )


# ── 内部:生成进度回写(agent 编排循环调用;X-Internal-Token 闸)──


class ProgressIn(BaseModel):
    report_id: str
    phase: str
    # 进度回写锚定租户归属(与 collab-start 同款闸);缺省 default 兼容旧
    # agent(租户语义在写侧过滤兜底)
    tenant_key: str = "default"


@internal_router.post("/progress", status_code=204)
async def report_progress(
    body: ProgressIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    from kongming.internal.service import TenantNotFound
    from kongming.internal.service import resolve_tenant as _resolve_tenant
    from kongming.report.models import PHASES

    if body.phase not in PHASES:
        raise HTTPException(status_code=422, detail="INVALID_PHASE")
    try:
        tenant_id = await _resolve_tenant(session, body.tenant_key)
    except TenantNotFound:
        # 未知租户与归属不匹配同语义:静默无效(同形 204,不向内部通道回显存在性)
        return
    await SqlReportRepo(session).set_phase(
        report_id=body.report_id, phase=body.phase, tenant_id=tenant_id
    )
    await session.commit()
