"""rag 组合根:RagService 装配(公共工厂 toolcall 复用同源;Depends 包装供端点)。"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.db import get_session
from kongming.rag.adapters import RelationExpansionAdapter, TeamMembershipAdapter
from kongming.rag.admin_service import RagAdminService
from kongming.rag.agent_callback import get_agent_rag_client
from kongming.rag.cache import ContextCache
from kongming.rag.coverage_repository import RagCoverageRepository
from kongming.rag.repository import RagRepository
from kongming.rag.service import RagService
from kongming.redis_client import get_redis


def get_rag_service(session: AsyncSession) -> RagService:
    """RagService 公共组合根(toolcall 复用同源;单例工厂同调)。"""
    client = get_agent_rag_client(get_redis())  # 进程内单例,连接池跨请求复用
    from kongming.chat.repository import SuggestionRepository

    settings = get_settings()
    membership = TeamMembershipAdapter(session)  # 双窄端口共用一适配器
    return RagService(
        # 仓储与缓存使用部署配置的分词器，默认值为 simple。
        RagRepository(session, ts_config=settings.rag_fulltext_ts_config),
        embedder=client,
        cache=ContextCache(get_redis(), ts_config=settings.rag_fulltext_ts_config),
        # 知识写路径级联失效建议缓存(DELETE 同事务,rag commit 收口)
        suggestion_evictor=SuggestionRepository(session),
        # 召回闸门配置(Settings 恒注入;None=测试轻量装配)
        gate_settings=settings,
        # 关系感知召回扩展(缺省开启;kill switch=rag_expand_enabled)
        relation_expander=RelationExpansionAdapter(session),
        expand_enabled=settings.rag_expand_enabled,
        expand_max=settings.rag_expand_max,
        # 可见集合的成员关系端口(只读消费 team 域)
        member_teams=membership,
    )


def get_rag_service_dep(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RagService:
    return get_rag_service(session)


def get_rag_admin_service(session: AsyncSession) -> RagAdminService:
    """管理簇组合根(与召回服务同源端口;不注入闸门/关系扩展——管理面零消费)。"""
    client = get_agent_rag_client(get_redis())
    from kongming.chat.repository import SuggestionRepository

    settings = get_settings()
    membership = TeamMembershipAdapter(session)
    return RagAdminService(
        RagRepository(session, ts_config=settings.rag_fulltext_ts_config),
        embedder=client,
        describer=client,
        cache=ContextCache(get_redis(), ts_config=settings.rag_fulltext_ts_config),
        suggestion_evictor=SuggestionRepository(session),
        member_teams=membership,
        member_roles=membership,
        coverage=RagCoverageRepository(session),  # 覆盖报告只读读面同会话
    )


def get_rag_admin_service_dep(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RagAdminService:
    return get_rag_admin_service(session)
