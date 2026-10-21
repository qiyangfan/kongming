"""feedback 路由(薄适配层):提交端点挂 /chat 前缀(spec 既定路径),
管理端点挂 /feedback 前缀。

权限点:提交=chat:feedback:submit;处置=system:feedback:handle;
涉及表/统计/分页=system:feedback:view。领域错误统一映射(403 同形提交面/
404 同形管理面,spec 分立约)。
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.router import current_user, require_perm
from kongming.db import get_session
from kongming.feedback.repository import FeedbackRepository
from kongming.feedback.service import (
    FeedbackForbidden,
    FeedbackNotFound,
    FeedbackService,
    InvalidCorrectedSql,
    InvalidHandleStatus,
    MessageNotAssistant,
    SamplePromotionUnavailable,
    TenantForbidden,
)

chat_router = APIRouter(prefix="/chat", tags=["chat"])
router = APIRouter(prefix="/feedback", tags=["feedback"])

# 以下类型在请求处理时用于注解和异常分支，需在运行时导入。
from kongming.rag.ports import EmbeddingError, EmbeddingPort, MemberTeamsReader  # noqa: E402
from kongming.rag.repository import RagRepository  # noqa: E402
from kongming.rag.service import RagService  # noqa: E402


def get_feedback_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FeedbackService:
    """组合根:rag 客户端只服务样本闸门(embedding + ctx 失效);
    组合形态与 chat/router.get_rag_service 同构。"""
    from kongming.config import get_settings
    from kongming.rag.agent_callback import get_agent_rag_client
    from kongming.rag.cache import ContextCache
    from kongming.redis_client import get_redis

    client = get_agent_rag_client(get_redis())
    # 仓储与缓存注入部署分词配置(未配置时缺省 simple)
    ts = get_settings().rag_fulltext_ts_config
    # 伴生:成员关系只读端口(与 rag 组合根同款适配,一实例两处消费)
    membership = _TeamMembershipAdapter(session)
    rag = RagService(
        RagRepository(session, ts_config=ts),
        embedder=client,
        describer=client,
        cache=ContextCache(get_redis(), ts_config=ts),
        member_teams=membership,
    )
    return FeedbackService(
        FeedbackRepository(session),
        sample_gate=_FeedbackSampleGate(
            RagRepository(session, ts_config=ts), client, rag, member_teams=membership
        ),
    )


class _TeamMembershipAdapter:
    """伴生组合根适配器(与 rag/router 同款):成员团队集合只读窄端口,
    实现=team 域 SqlTeamRepo 组合适配(只读消费,team 域零改动)。"""

    def __init__(self, session: AsyncSession) -> None:
        from kongming.team.repository import SqlTeamRepo

        self._repo = SqlTeamRepo(session)

    async def team_ids_of_user(self, user_id: str) -> list[str]:
        return list(await self._repo.role_map(user_id=user_id))


class _FeedbackSampleGate:
    """反馈样本启停端口的组合根实现，通过反馈仓储和向量化服务完成审核晋升。"""

    def __init__(
        self,
        repo: RagRepository,
        embedder: EmbeddingPort,
        rag: RagService,
        member_teams: MemberTeamsReader | None = None,  # 伴生:样本可见集合
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._rag = rag
        self._member_teams = member_teams

    async def promote(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        feedback_message_id: str,
        caller_user_id: str | None = None,
    ) -> None:
        try:
            vector = (await self._embedder.embed([question]))[0]
        except EmbeddingError as e:
            raise SamplePromotionUnavailable(str(e)) from e
        # 伴生:样本可见集合=反馈发起者所在团队(缺省/无端口=公共)
        visible_teams: list[str] = []
        if caller_user_id and self._member_teams is not None:
            visible_teams = sorted(await self._member_teams.team_ids_of_user(caller_user_id))
        await self._repo.promote_feedback_sample(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            sql_text=sql_text,
            feedback_message_id=feedback_message_id,
            embedding=vector,
            visible_teams=visible_teams,
        )

    async def disable(self, *, feedback_message_id: str) -> None:
        await self._repo.disable_feedback_samples(feedback_message_id=feedback_message_id)

    async def evict(self, *, tenant_id: str, datasource_key: str) -> None:
        """晋升/停用提交后 ctx 缓存按租户+数据源精确失效。"""
        await self._rag.evict_ctx(tenant_id, datasource_key)


# ============ 反馈提交(feedback spec;/chat 前缀) ============


class FeedbackIn(BaseModel):
    rating: Literal["UP", "DOWN"]
    comment: str | None = Field(default=None, max_length=500)
    corrected_sql: str | None = Field(default=None, max_length=4000)


class FeedbackOut(BaseModel):
    """提交后当前反馈状态(前端激活态回显用)。"""

    rating: str
    comment: str | None
    corrected_sql: str | None


@chat_router.post(
    "/messages/{message_id}/feedback",
    response_model=FeedbackOut,
    dependencies=[Depends(require_perm("chat:feedback:submit"))],
)
async def submit_feedback(
    req: FeedbackIn,
    message_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[FeedbackService, Depends(get_feedback_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> FeedbackOut:
    try:
        state = await svc.submit(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            message_id=str(message_id),
            rating=req.rating,
            comment=req.comment,
            corrected_sql=req.corrected_sql,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except FeedbackForbidden:
        raise HTTPException(status_code=403, detail="FEEDBACK_FORBIDDEN") from None
    except MessageNotAssistant:
        raise HTTPException(status_code=400, detail="MESSAGE_NOT_ASSISTANT") from None
    except InvalidCorrectedSql as e:
        raise HTTPException(status_code=400, detail=f"CORRECTED_SQL_REJECTED: {e}") from None
    return FeedbackOut(
        rating=state.rating, comment=state.comment, corrected_sql=state.corrected_sql
    )


# ============ 处置(feedback-refine spec) ============


class HandleIn(BaseModel):
    status: str = Field(min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=500)


class HandleOut(BaseModel):
    status: str
    note: str | None


@router.put(
    "/{message_id}/status",
    response_model=HandleOut,
    dependencies=[Depends(require_perm("system:feedback:handle"))],
    responses={
        503: {
            "description": (
                "样例晋升所需处理不可用时整体回滚(detail=SAMPLE_PROMOTION_UNAVAILABLE)。"
            )
        }
    },
)
async def handle_feedback(
    req: HandleIn,
    message_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[FeedbackService, Depends(get_feedback_service)],
) -> HandleOut:
    try:
        merged_note = await svc.handle(
            actor_id=str(user["sub"]),
            message_id=str(message_id),
            status=req.status,
            note=req.note,
        )
    except InvalidHandleStatus:
        raise HTTPException(status_code=400, detail="HANDLE_STATUS_INVALID") from None
    except SamplePromotionUnavailable:
        # 样例晋升所需处理失败时整体回滚,不提交反馈状态、审计或 query_history。
        raise HTTPException(status_code=503, detail="SAMPLE_PROMOTION_UNAVAILABLE") from None
    except FeedbackNotFound:
        raise HTTPException(status_code=404, detail="FEEDBACK_NOT_FOUND") from None
    return HandleOut(status=req.status, note=merged_note)


# ============ 涉及表定位(feedback-refine spec) ============


class InvolvedTableOut(BaseModel):
    table_name: str
    schema_name: str
    datasource_key: str
    has_knowledge: bool
    knowledge_id: str | None


class InvolvedTablesOut(BaseModel):
    tables: list[InvolvedTableOut]
    parse_error: bool


@router.get(
    "/{message_id}/involved-tables",
    response_model=InvolvedTablesOut,
    dependencies=[Depends(require_perm("system:feedback:view"))],
)
async def involved_tables(
    message_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[FeedbackService, Depends(get_feedback_service)],
) -> InvolvedTablesOut:
    try:
        view = await svc.involved_tables(actor_id=str(user["sub"]), message_id=str(message_id))
    except FeedbackNotFound:
        raise HTTPException(status_code=404, detail="FEEDBACK_NOT_FOUND") from None
    return InvolvedTablesOut(
        tables=[
            InvolvedTableOut(
                table_name=t.table_name,
                schema_name=t.schema_name,
                datasource_key=t.datasource_key,
                has_knowledge=t.has_knowledge,
                knowledge_id=t.knowledge_id,
            )
            for t in view.tables
        ],
        parse_error=view.parse_error,
    )


# ============ 统计概览(feedback-analytics spec) ============


class TrendItemOut(BaseModel):
    day: str
    up: int
    down: int


class OverviewOut(BaseModel):
    total: int
    up_count: int
    down_count: int
    corrected_count: int
    down_rate: float
    trend: list[TrendItemOut]


@router.get(
    "/overview",
    response_model=OverviewOut,
    dependencies=[Depends(require_perm("system:feedback:view"))],
)
async def overview(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[FeedbackService, Depends(get_feedback_service)],
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    datasource_key: Annotated[str | None, Query()] = None,
) -> OverviewOut:
    data = await svc.overview(
        actor_id=str(user["sub"]),
        days=days,
        tenant_id=None if tenant_id is None else str(tenant_id),
        datasource_key=datasource_key,
    )
    return OverviewOut(
        total=data["total"],
        up_count=data["upCount"],
        down_count=data["downCount"],
        corrected_count=data["correctedCount"],
        down_rate=data["downRate"],
        trend=[TrendItemOut(day=t["day"], up=t["up"], down=t["down"]) for t in data["trend"]],
    )


# ============ 明细分页(feedback-analytics spec) ============


class FeedbackPageItemOut(BaseModel):
    message_id: str
    session_id: str
    user_id: str
    user_nickname: str | None
    # 昵称缺失时前端回退登录用户名,禁渲染 id 片段
    user_username: str | None = None
    tenant_id: str
    tenant_key: str
    datasource_key: str
    rating: str | None
    question: str | None
    sql_text: str | None
    corrected_sql: str | None
    feedback_comment: str | None
    feedback_status: str
    feedback_time: datetime | None


class FeedbackPageOut(BaseModel):
    total: int
    items: list[FeedbackPageItemOut]


@router.get(
    "/page",
    response_model=FeedbackPageOut,
    dependencies=[Depends(require_perm("system:feedback:view"))],
)
async def page(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[FeedbackService, Depends(get_feedback_service)],
    page_num: Annotated[int, Query(ge=1, alias="page")] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    rating: Annotated[Literal["UP", "DOWN"] | None, Query()] = None,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    datasource_key: Annotated[str | None, Query()] = None,
    feedback_status: Annotated[
        Literal["PENDING", "ADOPTED", "REJECTED", "DISMISSED"] | None, Query()
    ] = None,
    only_corrected: Annotated[bool, Query()] = False,
    start_time: Annotated[datetime | None, Query()] = None,
    end_time: Annotated[datetime | None, Query()] = None,
) -> FeedbackPageOut:
    total, rows = await svc.page(
        actor_id=str(user["sub"]),
        user_id=None if user_id is None else str(user_id),
        rating=rating,
        tenant_id=None if tenant_id is None else str(tenant_id),
        datasource_key=datasource_key,
        feedback_status=feedback_status,
        only_corrected=only_corrected,
        start_time=start_time,
        end_time=end_time,
        page=page_num,
        size=size,
    )
    return FeedbackPageOut(
        total=total,
        items=[
            FeedbackPageItemOut(
                message_id=str(r["message_id"]),
                session_id=str(r["session_id"]),
                user_id=str(r["user_id"]),
                user_nickname=None if r["user_nickname"] is None else str(r["user_nickname"]),
                user_username=(None if r["user_username"] is None else str(r["user_username"])),
                tenant_id=str(r["tenant_id"]),
                tenant_key=str(r["tenant_key"]),
                datasource_key=str(r["datasource_key"]),
                rating=None if r["rating"] is None else str(r["rating"]),
                question=None if r["question"] is None else str(r["question"]),
                sql_text=None if r["sql_text"] is None else str(r["sql_text"]),
                corrected_sql=None if r["corrected_sql"] is None else str(r["corrected_sql"]),
                feedback_comment=(
                    None if r["feedback_comment"] is None else str(r["feedback_comment"])
                ),
                feedback_status=str(r["feedback_status"]),
                feedback_time=r["feedback_time"],  # type: ignore[arg-type]
            )
            for r in rows
        ],
    )
