"""chat 组合根:FastAPI 依赖工厂,装配 ChatService/SuggestionsService 与成本闸。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.chat.agent_client import AgentClient
from kongming.chat.costgate import ChatCostGate
from kongming.chat.inflight import InflightTurns
from kongming.chat.memory_tasks import extract_memory_task
from kongming.chat.ports import StreamStore, StreamStoreFactory
from kongming.chat.repository import ChatRepository, SuggestionRepository
from kongming.chat.scoring_tasks import score_answer_task
from kongming.chat.service import ChatService
from kongming.chat.session_service import ChatSessionService
from kongming.chat.suggestions import SuggestionsService
from kongming.chat.title_tasks import rewrite_session_title
from kongming.chat.wiring import SchemaOverviewSource
from kongming.config import get_settings
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.db import get_session, get_sessionmaker
from kongming.rag.cache import ContextCache
from kongming.rag.repository import RagRepository
from kongming.rag.service import RagService
from kongming.redis_client import get_redis
from kongming.toolcall.ratelimit import RedisRateLimiter


def get_agent_client() -> AgentClient:
    return AgentClient(get_settings())


def get_rag_service(session: AsyncSession) -> RagService:
    from kongming.rag.agent_callback import get_agent_rag_client
    from kongming.redis_client import get_redis

    client = get_agent_rag_client(get_redis())  # 进程内单例,连接池跨请求复用
    settings = get_settings()
    return RagService(
        # 仓储与缓存使用部署配置的分词器，默认值为 simple。
        RagRepository(session, ts_config=settings.rag_fulltext_ts_config),
        embedder=client,
        cache=ContextCache(get_redis(), ts_config=settings.rag_fulltext_ts_config),
        # 知识写路径级联失效建议缓存(DELETE 同事务,rag commit 收口)
        suggestion_evictor=SuggestionRepository(session),
        # 召回闸门配置(off 缺省)
        gate_settings=settings,
    )


# 在途轮登记是进程内单例:取消请求与发起请求落在同一进程才命中(单节点部署形态的前提)
_TURNS = InflightTurns()


def _build_cost_gate() -> ChatCostGate:
    """成本双闸组合根(limiter=Redis 固定窗口;counter=上条适配;
    oper=SqlOperLogRepo 独立短会话;参数取自 settings,0=不限)。"""
    from kongming.chat.wiring import ChatUsageCounter

    settings = get_settings()

    class _Oper:
        """审计注入(SqlOperLogRepo 独立短会话;bot 面 _QuotaOper 同范式)。"""

        async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
            from kongming.auth.repository import SqlOperLogRepo

            async with get_sessionmaker()() as s:
                await SqlOperLogRepo(s).add(None, action, target, detail)
                # 独立短会话必须显式提交:close 即回滚,缺 commit=审计静默丢失
                # (quality 域同款翻车的收口形态)
                await s.commit()

    return ChatCostGate(
        limiter=RedisRateLimiter(get_redis()),
        counter=ChatUsageCounter(),
        oper_logger=_Oper(),
        rate_per_minute=settings.chat_rate_per_minute,
        quota_daily_calls=settings.chat_quota_daily_calls,
        quota_monthly_calls=settings.chat_quota_monthly_calls,
    )


def get_chat_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[AgentClient, Depends(get_agent_client)],
) -> ChatService:
    from kongming.chat.wiring import (
        AttachmentChecker,
        ChatGuardrail,
        InternalExecuteRunner,
        InternalExplainRunner,
        MemoryAdapter,
        PendingToolReader,
        RegistryToolSpecProvider,
    )

    # 断线续存:stream_store_factory 开独立会话落库(title_tasks 同款模式)
    # tool_specs/pending_tools 绑定 toolcall 域(注册表过滤+待确认聚合,
    # 均独立短会话——同步/流式路径通用,不随请求作用域会话取消)
    return ChatService(
        ChatRepository(session),
        agent,
        SqlDatasourceRepo(session),
        history_writer=get_rag_service(session),
        explainer=InternalExplainRunner(session),
        stream_store_factory=_stream_store_factory(agent),
        tool_specs=RegistryToolSpecProvider(),
        pending_tools=PendingToolReader(),
        guardrail=ChatGuardrail(),  # 输入/输出护栏
        memory=MemoryAdapter(),  # 用户长期记忆
        runner=InternalExecuteRunner(session),  # 再执行
        attachment_checker=AttachmentChecker(session),
        implicit_memory_task=extract_memory_task,  # 隐式提取(组合根绑定)
        scoring_task=(
            # 答案异步评分(组合根绑定;开关关闭=不注入,逐字回引入前行为)
            score_answer_task if get_settings().answer_scoring_enabled else None
        ),
        column_comments=get_rag_service(session),  # 列标签(同一 RagService 的窄端口视角)
        cost_gate=_build_cost_gate(),  # 成本双闸(速率+配额)
        turns=_TURNS,  # 在途轮登记(取消端点与流式消费共用同一张表)
    )


def get_session_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatSessionService:
    """会话管理/导出组合根:导出审计走请求会话的操作日志仓储。"""
    return ChatSessionService(
        ChatRepository(session),
        oper_logger=SqlOperLogRepo(session),
    )


def get_schema_overview(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SchemaOverviewSource:
    return SchemaOverviewSource(session)


def get_suggestor() -> AgentClient:
    """Suggestor 端口绑定(AgentClient.suggest_questions 结构化匹配;
    独立依赖便于测试替身注入,与 get_agent_client 同款)。"""
    return AgentClient(get_settings())


def get_suggestions_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    overview: Annotated[SchemaOverviewSource, Depends(get_schema_overview)],
    suggestor: Annotated[AgentClient, Depends(get_suggestor)],
) -> SuggestionsService:
    return SuggestionsService(
        tenants=ChatRepository(session),
        suggestions=SuggestionRepository(session),
        ds_checker=SqlDatasourceRepo(session),
        overview=overview,
        suggestor=suggestor,
    )


def _stream_store_factory(agent: AgentClient) -> StreamStoreFactory:
    """断线续存落库单元工厂(design §4):独立 sessionmaker 短会话——
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
