"""toolcall 组合根:服务装配与网关进程单例。

工厂簇同命名空间收口:get_tool_service 按模块全局名调
_build_websearch_client/_build_connector_gate,测试 monkeypatch 靶点
与全局名查找必须落在同一模块(拆分散落会让替身静默失效)。
"""

import logging
import shutil
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlPermRepo
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_session
from kongming.mcp.breaker import build_mcp_caller, get_mcp_breaker
from kongming.mcp.repository import SqlMcpRepo
from kongming.mcp.service import McpService
from kongming.redis_client import get_redis
from kongming.sandbox.repository import SqlSandboxRunRepo
from kongming.sandbox.runner import PodmanSandboxRunner
from kongming.sandbox.service import SandboxService
from kongming.toolcall import registry
from kongming.toolcall.adapters import (
    ChatReplyStore,
    ConnectorToolGate,
    GatewayWebSearchClient,
    GuardrailIntentReviewer,
    InternalSqlExecutor,
    McpDynamicResolver,
    McpToolCaller,
    RagServiceAdapter,
)
from kongming.toolcall.ratelimit import RedisRateLimiter
from kongming.toolcall.repository import SqlToolCallRepo, ToolAuditWriter
from kongming.toolcall.service import ToolCallService, ToolLimits, ToolPorts
from kongming.toolgateway.gateway import Executor, ToolGateway
from kongming.toolgateway.models import (
    DOMAIN_TOOLS,
    WEBSEARCH_PROVIDERS,
    DomainPolicy,
    load_manifest,
)
from kongming.toolgateway.websearch import WebSearchExecutor, WebSearchPolicy

log = logging.getLogger(__name__)


def _build_sandbox() -> SandboxService | None:
    """组合根:enabled 且宿主 podman 可用才接 runner;
    否则 None(handler 收敛 ERROR「沙箱未启用」+审计,fail-closed 不裸 500)。
    模块级 import 安全:sandbox 域零副作用(podman 调用在运行期子进程)。"""
    settings = get_settings()
    if not settings.sandbox_enabled:
        return None
    if shutil.which("podman") is None:
        log.warning("KM_SANDBOX_ENABLED=true 但宿主无 podman,沙箱按未启用降级")
        return None
    return SandboxService(
        runner=PodmanSandboxRunner(
            image=settings.sandbox_image,
            timeout_sec=settings.sandbox_timeout_sec,
            mem_mb=settings.sandbox_mem_mb,
            cpus=settings.sandbox_cpus,
        ),
        writer=SqlSandboxRunRepo(),
    )


def build_mcp_service(session: AsyncSession) -> McpService:
    """mcp 域服务装配(动态解析与出站执行共用同一服务实例)。"""
    settings = get_settings()
    return McpService(
        SqlMcpRepo(session, get_cipher(settings.datasource_encrypt_key)),
        build_mcp_caller(settings),
        breaker=get_mcp_breaker(),
    )


def build_gateway() -> ToolGateway | None:
    """网关域清单装配(各域注册条件独立判定;条件不满足的域不进
    清单=零感知)。清单/策略器/执行器三件齐备才注册;策略器缺失的域不注册
    (fail-closed)。全部域条件均不满足 → None(无网关,既有路径)。"""
    settings = get_settings()
    entries: list[dict[str, object]] = []
    # websearch 域:供应商在注册表内 + API key 齐备两道条件;
    # 端点/白名单由供应商单源派生(部署不可单独改端点)
    provider = (settings.websearch_provider or "").strip().lower()
    api_key = (settings.websearch_api_key or "").strip()
    if provider in WEBSEARCH_PROVIDERS and api_key:
        spec = WEBSEARCH_PROVIDERS[provider]
        entries.append(
            {
                "id": "websearch",
                "endpoint": spec.endpoint,
                "whitelist_hosts": [spec.host],
                "timeout_ms": settings.websearch_timeout_ms,
                "quota_per_minute": settings.toolgateway_websearch_quota,
                "redaction": "allowlist:websearch",
            }
        )
    if not entries:
        return None
    manifest, warnings = load_manifest(entries)
    for w in warnings:
        log.warning("%s", w)
    policies: dict[str, DomainPolicy] = {}
    domain_executors: dict[str, Executor] = {}
    executor: Executor | None = None
    if "websearch" in manifest:
        policies["websearch"] = WebSearchPolicy(
            whitelist_hosts=manifest["websearch"].whitelist_hosts
        )
        domain_executors["websearch"] = WebSearchExecutor(adapter=provider, api_key=api_key)
    return ToolGateway(
        manifest=manifest,
        policies=policies,
        executor=executor,
        domain_executors=domain_executors or None,
    )


@lru_cache(maxsize=1)
def get_gateway() -> ToolGateway | None:
    """网关进程单例(停用集合跨请求一致;清单随部署配置,重启复位)。"""
    return build_gateway()


def _build_websearch_client() -> GatewayWebSearchClient | None:
    """供应商+API key 任一缺失 → 域不在清单 → None(工具不注册)。"""
    gw = get_gateway()
    return GatewayWebSearchClient(gw) if gw is not None and gw.declared("websearch") else None


def _build_connector_gate(session: AsyncSession) -> ConnectorToolGate:
    """装配缝(测试可 monkeypatch,与 _build_websearch_client 同构)。"""
    return ConnectorToolGate(session)


def get_tool_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ToolCallService:
    settings = get_settings()
    mcp_svc = build_mcp_service(session)
    return ToolCallService(
        ToolPorts(
            repo=SqlToolCallRepo(session),
            writer=ToolAuditWriter(),
            perms=SqlPermRepo(session),
            limiter=RedisRateLimiter(get_redis()),
            sql=InternalSqlExecutor(session),
            rag=RagServiceAdapter(session),
            reply=ChatReplyStore(session),
            intent=GuardrailIntentReviewer(session),  # 意图审核
            sandbox=_build_sandbox(),
            resolver=McpDynamicResolver(mcp_svc),
            mcp=McpToolCaller(mcp_svc),
            websearch=_build_websearch_client(),  # 未配置时返回 None。
            connector=_build_connector_gate(session),  # 未配置连接器时解析结果为 None。
        ),
        limits=ToolLimits(
            rate_per_minute=settings.tool_rate_limit_per_minute,
            confirm_ttl_minutes=settings.tool_confirm_ttl_minutes,
            websearch_quota=settings.toolgateway_websearch_quota,  # 域配额(独立分桶)
            connector_quota=settings.toolgateway_http_quota,  # 域配额(独立分桶)
        ),
    )


def sync_runtime_disabled() -> None:
    """停用域 → 其工具从注入集与分发解析同源消失(spec 域治理面场景;
    DOMAIN_TOOLS 反演域下工具集)。"""
    gw = get_gateway()
    disabled_domains: set[str] = set()
    if gw is not None:
        disabled_domains = {str(d["id"]) for d in gw.summary() if bool(d.get("disabled"))}
    registry.set_runtime_disabled(
        frozenset(t for t, d in DOMAIN_TOOLS.items() if d in disabled_domains)
    )


async def load_persisted_domain_states() -> None:
    """0044:启动装载持久停用集(lifespan 调用;测试可直调模拟重启)。
    库为运维意图单源:装载=替换语义;无网关(清单空)静默跳过。
    装载后同步运行时注册表(sync_runtime_disabled,与 disable 端点同口径)——
    否则重启后停用域的工具仍入注入集、分发仍可解析,持久化语义静默降级为
    「执行期才被网关拒」。"""
    gw = get_gateway()
    if gw is None:
        return
    from kongming.db import get_sessionmaker
    from kongming.toolcall.repository import list_domain_states

    async with get_sessionmaker()() as session:
        states = await list_domain_states(session)
    gw.apply_disabled(frozenset(d for d, dis in states.items() if dis))
    sync_runtime_disabled()
