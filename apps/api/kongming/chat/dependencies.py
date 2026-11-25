"""chat 组合根:FastAPI 依赖工厂,装配 ChatService/SuggestionsService 与成本闸。"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
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
from kongming.chat.turn_federation import RedisTurnFederation
from kongming.chat.wiring import SchemaOverviewSource
from kongming.config import get_settings
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.db import get_session, get_sessionmaker
from kongming.rag.cache import ContextCache
from kongming.rag.repository import RagRepository
from kongming.rag.service import RagService
from kongming.redis_client import get_redis
from kongming.toolcall.ratelimit import RedisRateLimiter
from kongming.voice.audit_sink import SessionOperLogSink
from kongming.voice.binding import VoiceTurnRegistry
from kongming.voice.repository import HttpxSignalingGateway, RedisAdmissionGate, RedisTicketStore
from kongming.voice.service import VoiceService, VoiceSettingsView


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


# 在途轮登记是进程内单例:取消请求与发起请求落在同一进程即命中(快路径)
_TURNS = InflightTurns()

# 跨实例在途协作(share-turn-registry):把在途登记共享出去,取消落到别的实例时
# 凭它转发给持有者。Redis 客户端惰性连接,进程内单例跨请求复用连接池。
_FEDERATION = RedisTurnFederation(get_redis(), get_settings())


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
        federation=_FEDERATION,
        voice_binding=_VOICE_REGISTRY,  # 语音轮关联字段(签发/绑定/终态回填)
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


# ============ 语音通话(voice 域) ============

# 进程内单例:在途通话表在 service 实例上,须跨请求存续(与 _TURNS/_FEDERATION 同范式)
_VOICE_GATE = RedisAdmissionGate(get_redis(), get_settings())
_VOICE_GATEWAY = HttpxSignalingGateway(get_settings())
_VOICE_TICKETS = RedisTicketStore(get_redis())
_VOICE_OPERLOG = SessionOperLogSink()
_VOICE_REGISTRY = VoiceTurnRegistry()
_RUNTIME_HOOKS: dict[str, Callable[[str, str], Awaitable[None]] | None] = {"turn_canceler": None}


def _build_voice_service() -> VoiceService:
    s = get_settings()
    return VoiceService(
        _VOICE_GATE,
        _VOICE_GATEWAY,
        _VOICE_TICKETS,
        operlog=_VOICE_OPERLOG,
        settings=VoiceSettingsView(
            voice_api_key=s.voice_api_key,
            voice_realtime_base_url=s.voice_realtime_base_url,
            voice_model=s.voice_model,
            voice_max_call_seconds=s.voice_max_call_seconds,
        ),
    )


# 服务实例必须单例:在途通话表(calls 字典)挂在实例上,建立/通道/挂断若各持
# 各的实例,通道永远找不到在途通话(4404/404 级联,占位无人释放)
_VOICE_SERVICE = _build_voice_service()


def get_voice_service() -> VoiceService:
    """语音服务进程内单例:准入/牵线/票据/审计的端口绑定单点。"""
    return _VOICE_SERVICE


VoiceServiceDep = Annotated[VoiceService, Depends(get_voice_service)]


def get_turns_registry() -> InflightTurns:
    """在途轮登记表单例(语音侧取消注入复用同一张表)。"""
    return _TURNS


def get_voice_registry() -> VoiceTurnRegistry:
    """语音轮关联字段注册表单例。"""
    return _VOICE_REGISTRY


def get_tickets_store() -> RedisTicketStore:
    """通道票据存储单例(WebSocket 适配层取用)。"""
    return _VOICE_TICKETS


def get_turn_canceler() -> Callable[[str, str], Awaitable[None]] | None:
    """语音侧系统取消端口:组合根注入 ChatService.cancel_turn 的窄签名。"""
    return _RUNTIME_HOOKS["turn_canceler"]


def install_turn_canceler(fn: Callable[[str, str], Awaitable[None]]) -> None:
    """启动时注入(依赖方向 voice→chat,经窄签名反转)。"""
    _RUNTIME_HOOKS["turn_canceler"] = fn
