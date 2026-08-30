"""chat 路由(薄适配层):POST /chat/query + 会话列表/消息历史/删除(006)
+ 重命名/搜索/导出/执行计划(010)+ 流式问答(017)。

首个受权限点保护的端点(chat:execute,rbac spec 权限点校验机制首发);
租户上下文取 X-Tenant-Id 头(multitenant spec),缺省取用户默认租户。
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.attachment.repository import SqlAttachmentRepo
from kongming.auth.repository import SqlOperLogRepo, SqlPermRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.agent_client import AgentClient
from kongming.chat.memory_tasks import extract_memory_task
from kongming.chat.ports import (
    ExplainOutcome,
    SqlRunOutcome,
    StreamStore,
    StreamStoreFactory,
)
from kongming.chat.repository import ChatRepository, SuggestionRepository
from kongming.chat.service import (
    AttachmentNotFound,
    ChatOutcome,
    ChatService,
    ExplainNoSql,
    InvalidDatasource,
    InvalidTitle,
    MessageNotFound,
    MessageResult,
    SessionMessage,
    SessionNotFound,
    StreamHandle,
    TenantForbidden,
)
from kongming.chat.suggestions import SuggestionsService
from kongming.chat.title_tasks import rewrite_session_title
from kongming.config import get_settings

if TYPE_CHECKING:
    from kongming.mcp.service import McpService
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.db import get_session, get_sessionmaker
from kongming.internal import service as internal_service
from kongming.internal.meta import MetaTableOut
from kongming.rag.cache import ContextCache
from kongming.rag.repository import RagRepository
from kongming.rag.service import RagService
from kongming.toolcall.repository import SqlToolCallRepo

router = APIRouter(prefix="/chat", tags=["chat"])

STREAM_HEARTBEAT_SEC = 15.0  # SSE 心跳间隔(017;慢查询防中间层空闲断连)

log = logging.getLogger(__name__)


def get_agent_client() -> AgentClient:
    return AgentClient(get_settings())


def get_rag_service(session: AsyncSession) -> RagService:
    from kongming.rag.agent_callback import get_agent_rag_client
    from kongming.redis_client import get_redis

    client = get_agent_rag_client(get_redis())  # 013:进程内单例,连接池跨请求复用
    return RagService(
        RagRepository(session),
        embedder=client,
        describer=client,
        cache=ContextCache(get_redis()),
        # 047:知识写路径级联失效建议缓存(DELETE 同事务,rag commit 收口)
        suggestion_evictor=SuggestionRepository(session),
    )


class _InternalExplainRunner:
    """组合根适配器(010):chat 域 ExplainRunner 端口 ← internal.service.explain。

    chat service/models 受 import-linter 零框架契约,不可间接 import internal
    (携 sqlalchemy);内部异常在此翻译为 chat 域异常,不越域泄漏。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def explain(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
    ) -> ExplainOutcome:
        try:
            r = await internal_service.explain(
                self._s,
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=sql_text,
                user_id=user_id,
                question=None,
            )
        except internal_service.InvalidDatasource as e:
            # 竞态兜底(chat 已预检):禁用/删除发生在预检与执行之间
            raise InvalidDatasource(str(e)) from e
        except (internal_service.TenantNotFound, internal_service.TenantBindingForbidden) as e:
            raise TenantForbidden(str(e)) from e
        return ExplainOutcome(verdict=r.verdict, reason=r.reason, plan=r.plan)


class _InternalExecuteRunner:
    """026 rerun 端口:复用 internal.service.execute 全链路(六层防御)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def run(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
    ) -> SqlRunOutcome:
        try:
            return await internal_service.execute(
                self._s,
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                sql_text=sql_text,
                user_id=user_id,
                question=None,
                limit=1000,
            )
        except internal_service.InvalidDatasource as e:
            # 竞态兜底(chat 已预检):禁用/删除发生在预检与执行之间(与 explain 适配器同范式)
            raise InvalidDatasource(str(e)) from e
        except (internal_service.TenantNotFound, internal_service.TenantBindingForbidden) as e:
            raise TenantForbidden(str(e)) from e


def get_chat_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
) -> ChatService:
    # history_writer=rag 回写端口(009;None 安全——注入失败不影响问答主链路)
    # explainer=执行计划端口(010,组合根绑定 internal.service.explain)
    # 013 构造反转:repo 在组合根装配,service 仅持 ChatRepo 端口(零框架契约)
    # 015 导出审计:oper_logger 绑定 auth 域 SqlOperLogRepo(007 组合模式)
    # 017 断线续存:stream_store_factory 开独立会话落库(title_tasks 同款模式)
    # 019:tool_specs/pending_tools 绑定 toolcall 域(注册表过滤+待确认聚合,
    # 均独立短会话——同步/流式路径通用,不随请求作用域会话取消)
    return ChatService(
        ChatRepository(session),
        agent,
        SqlDatasourceRepo(session),
        get_rag_service(session),
        _InternalExplainRunner(session),
        SqlOperLogRepo(session),
        _stream_store_factory(agent),
        _RegistryToolSpecProvider(),
        _PendingToolReader(),
        _ChatGuardrail(),  # 020 输入/输出护栏
        _MemoryAdapter(),  # 022 用户长期记忆
        _InternalExecuteRunner(session),  # 026 再执行
        attachment_checker=_AttachmentChecker(session),  # 030(关键字传参防错位)
        implicit_memory_task=extract_memory_task,  # 022 隐式提取(组合根绑定)
        column_comments=get_rag_service(session),  # 041 列标签(同一 RagService 的窄端口视角)
    )


class _TeamRolesAdapter:
    """032 TeamRoles 绑定(team 域 role_map;独立短会话)。"""

    async def role_map(self, user_id: str) -> dict[str, str]:
        from kongming.team.repository import SqlTeamRepo

        async with get_sessionmaker()() as s:
            return await SqlTeamRepo(s).role_map(user_id=user_id)


class _MemoryAdapter:
    """记忆端口绑定(022):独立短会话(同步/流式/后台任务通用);
    form_explicit 的敏感拒绝转 False(适配端口 bool 语义)。"""

    async def recall_hint(self, *, user_id: str, tenant_id: str, question: str) -> str:
        from kongming.memory.repository import SqlMemoryRepo
        from kongming.memory.service import MemoryService
        from kongming.rag.agent_callback import get_agent_rag_client
        from kongming.redis_client import get_redis

        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
                _TeamRolesAdapter(),
            )
            return await svc.recall_hint(
                user_id=user_id, tenant_id=tenant_id, question=question
            )

    async def explicit_intent(self, question: str) -> str | None:
        from kongming.memory.models import extract_explicit_intent

        return extract_explicit_intent(question)

    async def form_explicit(self, *, user_id: str, tenant_id: str, content: str) -> bool:
        from kongming.memory.repository import SqlMemoryRepo
        from kongming.memory.service import MemoryService, SensitiveContentRejected
        from kongming.rag.agent_callback import get_agent_rag_client
        from kongming.redis_client import get_redis

        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
                _TeamRolesAdapter(),
            )
            try:
                await svc.form_explicit(user_id=user_id, tenant_id=tenant_id, content=content)
            except SensitiveContentRejected:
                return False
        return True

    async def form_implicit(
        self, *, user_id: str, tenant_id: str, candidates: list[dict[str, str]]
    ) -> int:
        from kongming.memory.repository import SqlMemoryRepo
        from kongming.memory.service import MemoryService
        from kongming.rag.agent_callback import get_agent_rag_client
        from kongming.redis_client import get_redis

        async with get_sessionmaker()() as s:
            svc = MemoryService(
                SqlMemoryRepo(s),
                get_agent_rag_client(get_redis()),
                SqlOperLogRepo(s),
                _TeamRolesAdapter(),
            )
            return await svc.form_implicit(
                user_id=user_id, tenant_id=tenant_id, candidates=candidates
            )


class _AttachmentChecker:
    """030 AttachmentChecker 绑定(attachment 域仓储,请求会话)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = SqlAttachmentRepo(session)

    async def validate_owned(
        self, *, ids: list[str], user_id: str, tenant_id: str
    ) -> list[dict[str, object]] | None:
        out: list[dict[str, object]] = []
        # R1-F3:去重保序——同 id 重复引用过检后第二次取件必 404
        # (一次性消费),整轮 fail-closed 且文案误导
        for att_id in dict.fromkeys(ids):
            row = await self._repo.find_owned(
                att_id=att_id, tenant_id=tenant_id, user_id=user_id
            )
            if row is None or row["status"] != "UPLOADED":
                return None
            out.append(
                {
                    "id": str(row["id"]),
                    "filename": str(row["filename"]),
                    "mime": str(row["mime"]),
                    "size_bytes": int(row["size_bytes"]),
                }
            )
        return out


class _RegistryToolSpecProvider:
    """ReAct 注入工具的唯一真理源(019):权限点集查询 + toolcall 注册表过滤。
    028:合并租户 MCP 动态工具(持 tool:mcp:use 才可见——与执行面 L2 同判据)。"""

    async def authorized_tools(
        self, user_id: str, tenant_id: str
    ) -> list[dict[str, object]]:
        from kongming.toolcall.registry import filter_specs

        async with get_sessionmaker()() as s:
            codes = await SqlPermRepo(s).list_codes(user_id)
            specs: list[dict[str, object]] = [dict(spec) for spec in filter_specs(codes)]
            if "tool:mcp:use" in codes:
                mcp_svc = _build_mcp_service_for(s)
                for entry in await mcp_svc.list_dynamic_tools(tenant_id=tenant_id):
                    read_only = bool(entry.get("read_only"))
                    specs.append(
                        {
                            "name": str(entry.get("qualified") or ""),
                            "description": str(entry.get("description") or ""),
                            "parameters": (
                                entry.get("schema")
                                if isinstance(entry.get("schema"), dict)
                                else {"type": "object"}
                            ),
                            "write": not read_only,
                            "require_confirm": not read_only,
                        }
                    )
        return specs


def _build_mcp_service_for(session: AsyncSession) -> "McpService":
    """028 装配(chat 适配层;与 toolcall 组合根同构)。"""
    from kongming.datasource.cipher import get_cipher
    from kongming.mcp.client import HttpMcpCaller
    from kongming.mcp.repository import SqlMcpRepo
    from kongming.mcp.service import McpService

    settings = get_settings()
    return McpService(
        SqlMcpRepo(session, get_cipher(settings.datasource_encrypt_key)),
        HttpMcpCaller(timeout_sec=settings.mcp_outbound_timeout_sec),
    )


class _PendingToolReader:
    """待确认写工具聚合(019):独立短会话只读(PENDING_CONFIRM 行经独立事务提交)。"""

    async def list_pending(self, trace_id: str) -> list[dict[str, object]]:
        async with get_sessionmaker()() as s:
            return await SqlToolCallRepo(s).list_pending_by_trace(trace_id)


class _ChatGuardrail:
    """prompt 护栏端口绑定(020):独立短会话读配置;审计经 writer 独立事务。"""

    async def check_input(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]:
        from kongming.guardrail.repository import (
            SqlGuardrailAuditWriter,
            SqlGuardrailRepo,
        )
        from kongming.guardrail.service import GuardrailService

        async with get_sessionmaker()() as s:
            svc = GuardrailService(SqlGuardrailRepo(s), SqlGuardrailAuditWriter())
            verdict = await svc.evaluate_input(
                trace_id=trace_id, user_id=user_id, tenant_id=tenant_id, texts=texts
            )
        return verdict.blocked, verdict.processed

    async def filter_output(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> list[str]:
        from kongming.guardrail.repository import (
            SqlGuardrailAuditWriter,
            SqlGuardrailRepo,
        )
        from kongming.guardrail.service import GuardrailService

        async with get_sessionmaker()() as s:
            svc = GuardrailService(SqlGuardrailRepo(s), SqlGuardrailAuditWriter())
            return await svc.evaluate_outputs(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                texts=texts,
                safe_replacement="答案未通过安全检查,已省略。",
            )


def _stream_store_factory(agent: AgentClient) -> StreamStoreFactory:
    """断线续存落库单元工厂(017 design §4):独立 sessionmaker 短会话——
    请求作用域会话随 SSE 响应取消而关闭,后台任务落库不得复用。"""

    @asynccontextmanager
    async def _open() -> AsyncGenerator[StreamStore]:
        async with get_sessionmaker()() as s:
            yield StreamStore(
                repo=ChatRepository(s),
                history_writer=get_rag_service(s),
                trigger_title_rewrite=lambda sid: rewrite_session_title(sid, agent),
            )

    return _open


class ChatQueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: uuid.UUID | None = None
    persist: bool = True  # 显式 false=临时对话不落库(chat spec)
    # 条数与单条双限:防超大 history 撑爆 LLM prompt(token 成本攻击面)
    history: list[Annotated[str, Field(max_length=2000)]] = Field(
        default_factory=list, max_length=10
    )
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    # 019:Agent 内核选择(纯增,缺省 recipe 零回归);react=ReAct 工具调用内核
    agent_mode: Literal["recipe", "react"] = "recipe"
    # 030:图片附件引用(纯增,≤3;归属校验在 service)
    attachment_ids: list[uuid.UUID] = Field(
        default_factory=list[uuid.UUID], max_length=3
    )


class ChartOut(BaseModel):
    type: str
    x: str | None = None
    y: str | None = None


class ArtifactOut(BaseModel):
    """类型化产物(026):type ∈ sql/chart/document。"""

    type: str
    content: str
    metadata: dict[str, object] = {}


class PendingToolCallOut(BaseModel):
    """待确认写工具调用(019):确认卡片渲染要素。"""

    confirm_id: str
    tool_name: str
    args_digest: str = ""


class ChatQueryOut(BaseModel):
    session_id: str | None
    success: bool
    verdict: str | None
    summary: str
    insights: list[dict[str, str]]
    stats: dict[str, object]
    columns: list[str]
    rows: list[list[object]]
    sql: str
    chart: ChartOut
    retry_count: int
    error: str | None
    # 047(纯增):原始技术错误(FAILED 携带;前端折叠「技术详情」,缺省隐藏)
    error_detail: str | None = None
    # 014 messageId 打通(纯增):assistant 落库消息 id(反馈提交引用)+执行数据源快照
    message_id: str | None = None
    datasource_key: str = "default"
    # 019(纯增):待确认写工具调用(空列表=无)
    pending_tool_calls: list[PendingToolCallOut] = []
    # 026(纯增):类型化产物(空数组零回归)
    artifacts: list["ArtifactOut"] = []
    # 030(纯增):附件引用摘要(空数组零回归)
    attachments: list[dict[str, object]] = []
    # 041(纯增):呈现层列标签,与 columns 等长对齐(空=未接线/历史存量,回退 columns)
    column_labels: list[str] = []


def _out(o: ChatOutcome) -> ChatQueryOut:
    return ChatQueryOut(
        session_id=o.session_id,
        attachments=[dict(a) for a in o.attachments],
        success=o.success,
        verdict=o.verdict,
        summary=o.summary,
        insights=o.insights,
        stats=o.stats,
        columns=o.columns,
        rows=o.rows,
        sql=o.sql,
        chart=ChartOut(type=o.chart.type, x=o.chart.x, y=o.chart.y),
        retry_count=o.retry_count,
        error=o.error,
        error_detail=o.error_detail,
        message_id=o.message_id,
        datasource_key=o.datasource_key,
        pending_tool_calls=[
            PendingToolCallOut(
                confirm_id=str(p.get("confirm_id") or ""),
                tool_name=str(p.get("tool_name") or ""),
                args_digest=str(p.get("args_digest") or ""),
            )
            for p in o.pending_tool_calls
        ],
        artifacts=[
            ArtifactOut(
                type=str(a.get("type") or ""),
                content=str(a.get("content") or ""),
                metadata=(
                    # isinstance 守卫后 cast 收窄键型(mcp client 同款;运行时不变)
                    dict(cast("dict[str, object]", _meta))
                    if isinstance(_meta := a.get("metadata"), dict)
                    else {}
                ),
            )
            for a in o.artifacts
        ],
        column_labels=o.column_labels,
    )


@router.post(
    "/query",
    response_model=ChatQueryOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def chat_query(
    req: ChatQueryIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    background: BackgroundTasks,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ChatQueryOut:
    try:
        outcome = await svc.query(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            question=req.question,
            session_id=None if req.session_id is None else str(req.session_id),
            persist=req.persist,
            history=req.history[-5:],  # 防御性截断(agent 侧另有限制)
            datasource_key=req.datasource_key,
            agent_mode=req.agent_mode,
            attachment_ids=[str(a) for a in req.attachment_ids],
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except AttachmentNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    except InvalidDatasource:
        # 不存在/禁用/越属同形(防 key 探测);与 internal 面同一 detail 码
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    # 010 标题两阶段生成:首个 SUCCESS 轮后异步改写(守卫/条件写双闸在任务内);
    # persist=false 临时对话无会话可改写;REJECTED/FAILED 轮不触发(违规/失败问句不作素材)
    if outcome.success and req.persist and outcome.session_id is not None:
        background.add_task(rewrite_session_title, outcome.session_id, svc.agent_for_tasks)
        # 022 隐式记忆(首 SUCCESS 轮同款触发点;best-effort;
        # R1-M2:显式短路轮排除——防同内容再落一条 IMPLICIT)
        if outcome.tenant_id and not outcome.memory_shortcut:
            background.add_task(
                extract_memory_task,
                user_id=str(user["sub"]),
                tenant_id=outcome.tenant_id,
                question=req.question,
                answer=outcome.summary,
                agent=svc.agent_for_tasks,
            )
    return _out(outcome)


# ============ 对话冷启动建议问题(039 chat-suggestions) ============

_SUGGEST_TABLE_CAP = 30  # 摘要表数封顶(meta 自身另有 max_tables,双闸取严)
_SUGGEST_DIGEST_CHARS = 12_000  # 摘要字符封顶(防巨库撑爆生成 prompt)


def _render_schema_digest(
    tables: list[MetaTableOut], truncated: bool
) -> str:
    """内省结果 → 结构摘要文本(仅元数据,零行数据,ADR-002)。"""
    lines: list[str] = []
    for t in tables[:_SUGGEST_TABLE_CAP]:
        header = f"{t.schema_}.{t.name}"
        if t.comment:
            header += f" —— {t.comment}"
        lines.append(header)
        for c in t.columns:
            col = f"  - {c.name} {c.data_type}"
            if c.comment:
                col += f" -- {c.comment}"
            lines.append(col)
    text = "\n".join(lines)
    if truncated or len(tables) > _SUGGEST_TABLE_CAP:
        text += "\n(表过多,以上仅为部分)"
    return text[:_SUGGEST_DIGEST_CHARS]


class _SchemaOverviewSource:
    """组合根适配器(039):chat 域 SchemaOverviewSource ← internal/meta 同源
    内省(保留表过滤/注释/单 schema/default 直通语义全部单一真理源)。
    数据源不可用/内省失败 → None(service 收敛空清单,降级不 5xx)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def overview(self, *, tenant_key: str, datasource_key: str) -> str | None:
        from kongming.internal.meta import MetaSchemaIn, meta_schema

        try:
            out = await meta_schema(
                MetaSchemaIn(tenant_key=tenant_key, datasource_key=datasource_key),
                self._s,
            )
        except Exception:  # noqa: BLE001  # 生成链路失败降级空清单,不 5xx
            log.warning("建议问题 schema 摘要内省失败 ds=%s", datasource_key, exc_info=True)
            return None
        return _render_schema_digest(out.tables, out.truncated)


def get_schema_overview(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> _SchemaOverviewSource:
    return _SchemaOverviewSource(session)


def get_suggestor() -> AgentClient:
    """Suggestor 端口绑定(AgentClient.suggest_questions 结构化匹配;
    独立依赖便于测试替身注入,与 get_agent_client 同款)。"""
    return AgentClient(get_settings())


def get_suggestions_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    overview: Annotated[_SchemaOverviewSource, Depends(get_schema_overview)],
    suggestor: Annotated[AgentClient, Depends(get_suggestor)],
) -> SuggestionsService:
    return SuggestionsService(
        tenants=ChatRepository(session),
        suggestions=SuggestionRepository(session),
        ds_checker=SqlDatasourceRepo(session),
        overview=overview,
        suggestor=suggestor,
    )


class SuggestionsOut(BaseModel):
    """GET /chat/suggestions 响应:建议问句清单(空=无建议,静默隐藏)。"""

    items: list[str]


@router.get(
    "/suggestions",
    response_model=SuggestionsOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def chat_suggestions(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[SuggestionsService, Depends(get_suggestions_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    datasource_key: Annotated[
        str, Query(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    ] = "default",
) -> SuggestionsOut:
    try:
        items = await svc.list_suggestions(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            datasource_key=datasource_key,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return SuggestionsOut(items=items)


# ============ 流式问答(017 query-streaming) ============


class StreamStageOut(BaseModel):
    """stage 事件 data:节点粒度进度(status ∈ running/done)。"""

    node: str
    status: Literal["running", "done"]
    retry_count: int = 0  # selfheal=本次重试序号;其余节点=当前已重试次数


class StreamSqlOut(BaseModel):
    """sql 事件 data:validate 通过后提前下发,内容与终态落库 SQL 一致。"""

    sql: str


class StreamSummaryOut(BaseModel):
    """summary 事件 data:总结增量(当前为单条完整文案,逐事件追加=打字机语义)。"""

    delta: str


class StreamErrorOut(BaseModel):
    """error 终态事件 data:仅传输层/意外失败(业务三态走 result 事件)。"""

    error: str
    reason_code: str | None = None


class StreamToolCallOut(BaseModel):
    """tool_call 事件 data(019 tool-calling):工具调用进度。

    status ∈ running/done/rejected(confirm_required 以 done 下发,result_digest
    含「待确认」);args_digest/result_digest 为截断摘要(审计同源口径)。
    """

    tool_name: str
    status: Literal["running", "done", "rejected"]
    args_digest: str = ""
    result_digest: str = ""


# model 键使 FastAPI 把全部事件模型注册进 components(ADR-005 类型生成可达),
# content 键描述 SSE 事件流的真实形态(6 类事件 data 的 oneOf)
_STREAM_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "model": (
            StreamStageOut | StreamSqlOut | StreamSummaryOut
            | StreamToolCallOut | StreamErrorOut | ChatQueryOut
        ),
        "description": (
            "SSE 事件流:stage/sql/summary/tool_call 过程事件逐帧下发;result=data 与同步"
            " /chat/query 响应逐字段一致;error=传输层/意外失败终态;注释行(: ping)为心跳"
        ),
        "content": {
            "text/event-stream": {
                "schema": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/StreamStageOut"},
                        {"$ref": "#/components/schemas/StreamSqlOut"},
                        {"$ref": "#/components/schemas/StreamSummaryOut"},
                        {"$ref": "#/components/schemas/StreamToolCallOut"},
                        {"$ref": "#/components/schemas/StreamErrorOut"},
                        {"$ref": "#/components/schemas/ChatQueryOut"},
                    ]
                }
            }
        },
    }
}


def _sse_frame(event: str, data_json: str) -> str:
    return f"event: {event}\ndata: {data_json}\n\n"


async def _relay_gen(handle: StreamHandle) -> AsyncIterator[str]:
    """SSE 生成器:从后台任务队列转发帧;超时发心跳注释行;None 即收束。
    前端断开时本生成器被取消,后台任务(断线续存)继续不受影响。"""
    while True:
        try:
            item = await asyncio.wait_for(handle.queue.get(), timeout=STREAM_HEARTBEAT_SEC)
        except TimeoutError:
            yield ": ping\n\n"
            continue
        if item is None:
            return
        event, payload = item
        if event == "result" and isinstance(payload, ChatOutcome):
            yield _sse_frame("result", _out(payload).model_dump_json())
        else:
            yield _sse_frame(event, json.dumps(payload, ensure_ascii=False, default=str))


@router.post(
    "/query/stream",
    dependencies=[Depends(require_perm("chat:execute"))],
    response_class=StreamingResponse,
    responses=_STREAM_RESPONSES,
)
async def chat_query_stream(
    req: ChatQueryIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    try:
        handle = await svc.start_stream(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            question=req.question,
            session_id=None if req.session_id is None else str(req.session_id),
            persist=req.persist,
            history=req.history[-5:],
            datasource_key=req.datasource_key,
            agent_mode=req.agent_mode,
            attachment_ids=[str(a) for a in req.attachment_ids],
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except AttachmentNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    except InvalidDatasource:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    # 前段校验失败在流开始前以常规 JSON 错误响应(query-streaming spec「防线同构」)
    return StreamingResponse(
        _relay_gen(handle),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============ 会话列表/消息历史/删除(006,web-nav 支撑端点) ============


class SessionItemOut(BaseModel):
    id: str
    title: str
    updated_at: datetime


class ChartInfoOut(BaseModel):
    type: str
    x: str | None = None
    y: str | None = None


class MessageResultOut(BaseModel):
    columns: list[str] = Field(default_factory=list[str])
    rows: list[list[object]] = Field(default_factory=list[list[object]])
    insights: list[dict[str, str]] = Field(default_factory=list[dict[str, str]])
    stats: dict[str, object] = Field(default_factory=dict[str, object])
    chart: ChartInfoOut
    error: str | None = None
    # 047(纯增):原始技术错误(存量档缺失→None,前端不渲染折叠块)
    error_detail: str | None = None
    # 026(纯增):类型化产物
    artifacts: list[ArtifactOut] = []
    # 041(纯增):呈现层列标签(空=历史存量档,回退 columns)
    column_labels: list[str] = Field(default_factory=list[str])


class SessionMessageOut(BaseModel):
    id: str
    role: str
    question: str | None = None
    summary: str | None = None
    sql: str | None = None
    status: str
    error: str | None = None
    # 047(纯增):原始技术错误(FAILED 回显;web 折叠「技术详情」)
    error_detail: str | None = None
    result: MessageResultOut | None = None
    # 014 messageId 打通(纯增):assistant 消息反馈状态回显(user 消息恒 None)
    rating: str | None = None
    corrected_sql: str | None = None
    feedback_comment: str | None = None
    # 030(纯增):附件引用摘要
    attachments: list[dict[str, object]] = []


def _msg_out(m: SessionMessage) -> SessionMessageOut:
    result: MessageResultOut | None = None
    if m.result is not None:
        r: MessageResult = m.result
        result = MessageResultOut(
            columns=r.columns,
            rows=r.rows,
            insights=r.insights,
            stats=r.stats,
            chart=ChartInfoOut(type=r.chart.type, x=r.chart.x, y=r.chart.y),
            error=r.error,
            error_detail=r.error_detail,
            column_labels=r.column_labels,
            artifacts=[
                ArtifactOut(
                    type=str(a.get("type") or ""),
                    content=str(a.get("content") or ""),
                    metadata=(
                        # isinstance 守卫后 cast 收窄键型(与 _out 同款;运行时不变)
                        dict(cast("dict[str, object]", _meta))
                        if isinstance(_meta := a.get("metadata"), dict)
                        else {}
                    ),
                )
                for a in r.artifacts
            ],
        )
    return SessionMessageOut(
        id=m.id,
        role=m.role,
        question=m.question,
        summary=m.summary,
        sql=m.sql,
        status=m.status,
        error=m.error,
        error_detail=m.error_detail,
        result=result,
        rating=m.rating,
        corrected_sql=m.corrected_sql,
        feedback_comment=m.feedback_comment,
        attachments=[dict(a) for a in m.attachments],
    )


class RerunIn(BaseModel):
    """SQL 卡再执行(026):message_id 引用 + 编辑后 SQL;归属与六层防御在
    service/实现侧(与 010 EXPLAIN 同范式)。"""

    message_id: uuid.UUID
    sql: str = Field(min_length=1, max_length=4000)


@router.post(
    "/artifacts/rerun",
    response_model=ChatQueryOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def rerun_artifact(
    req: RerunIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ChatQueryOut:
    try:
        outcome = await svc.rerun_artifact(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            message_id=str(req.message_id),
            sql=req.sql,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except MessageNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except InvalidDatasource:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return _out(outcome)


@router.get(
    "/sessions",
    response_model=list[SessionItemOut],
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def list_sessions(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[SessionItemOut]:
    try:
        rows = await svc.list_sessions(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            limit=limit,
            keyword=q or "",
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    return [
        SessionItemOut(
            id=s.id, title=s.title, updated_at=s.updated_at  # type: ignore[arg-type]
        )
        for s in rows
    ]


class RenameIn(BaseModel):
    """重命名(010):去空白后非空与不超列上限(200)由领域层判定(013 补齐——
    spec 立约以 strip 后值为准,带空白 201 字 strip 后 ≤200 不再 422 误拒);
    max_length=2000 仅防离谱请求体(与 question 同宽),非业务上限。"""

    title: str = Field(min_length=1, max_length=2000)


@router.patch(
    "/sessions/{session_id}",
    response_model=SessionItemOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def rename_session(
    session_id: uuid.UUID,
    req: RenameIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> SessionItemOut:
    try:
        s = await svc.rename_session(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            session_id=str(session_id),
            title=req.title,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        # 不存在/越权/跨租户同形 404,不泄露会话存在性(chat spec)
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    except InvalidTitle:
        raise HTTPException(status_code=400, detail="TITLE_INVALID") from None
    return SessionItemOut(
        id=s.id, title=s.title, updated_at=s.updated_at  # type: ignore[arg-type]
    )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=list[SessionMessageOut],
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def session_messages(
    session_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[SessionMessageOut]:
    try:
        msgs = await svc.session_messages(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            session_id=str(session_id),
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        # 不存在/越权/跨租户同形 404,不泄露会话存在性(chat spec 006 delta)
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None
    return [_msg_out(m) for m in msgs]


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def delete_session(
    session_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    try:
        await svc.delete_session(
            user_id=str(user["sub"]), tenant_key=x_tenant_id, session_id=str(session_id)
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND") from None


# ============ 结果导出(010,chat spec「结果导出 CSV/Excel」) ============


class ExportIn(BaseModel):
    """所见即所导(010 design D3):前端传已展示的 columns+rows,不重跑 SQL。

    rows 传输层上限=R1 评审修复:截断发生在 JSON 解析之后,挡不住请求体本身
    的内存放大;max_length 在解析期即拒(422),service 层截断保留为双闸。
    """

    format: Literal["csv", "xlsx"]
    columns: list[str] = Field(min_length=1, max_length=200)
    rows: list[list[object]] = Field(
        default_factory=list[list[object]], max_length=10000
    )


@router.post(
    "/export",
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def export_result(
    req: ExportIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> Response:
    try:
        content, media_type, ext = await svc.export_result(
            user_id=str(user["sub"]),
            tenant_key=x_tenant_id,
            fmt=req.format,
            columns=req.columns,
            rows=req.rows,
            max_rows=get_settings().export_max_rows,
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    # 「查询结果-<时间戳>.<ext>」(chat spec);RFC 5987 filename* 携带中文名
    filename = quote(f"查询结果-{datetime.now():%Y%m%d-%H%M%S}.{ext}")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


# ============ 执行计划(010,chat spec「EXPLAIN 执行计划」) ============


class ExplainIn(BaseModel):
    """message_id 引用形态(010 design D2):客户端不可提交裸 SQL。"""

    message_id: uuid.UUID


class ExplainOut(BaseModel):
    success: bool
    plan: object | None = None  # EXPLAIN (FORMAT JSON) 解析产物(顶层数组)
    error: str | None = None


@router.post(
    "/explain",
    response_model=ExplainOut,
    dependencies=[Depends(require_perm("chat:execute"))],
)
async def explain_message(
    req: ExplainIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ChatService, Depends(get_chat_service)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ExplainOut:
    try:
        view = await svc.explain_message(
            user_id=str(user["sub"]), tenant_key=x_tenant_id, message_id=str(req.message_id)
        )
    except TenantForbidden:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN") from None
    except MessageNotFound:
        # 不存在/越权/跨租户同形 404,不泄露消息存在性(与会话端点同语义)
        raise HTTPException(status_code=404, detail="MESSAGE_NOT_FOUND") from None
    except ExplainNoSql:
        raise HTTPException(status_code=400, detail="EXPLAIN_NO_SQL") from None
    except InvalidDatasource:
        raise HTTPException(status_code=400, detail="DATASOURCE_INVALID") from None
    return ExplainOut(success=view.success, plan=view.plan, error=view.error)
