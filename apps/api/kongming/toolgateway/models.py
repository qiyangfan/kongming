"""toolgateway 领域模型(089):域清单装载 + 重试分类(零框架,宪法 Article II)。

域清单是数据:装载为可确定性重放的纯函数,畸形单域剔除并告警(fail-closed
不裸崩,不影响其余域);重试分类=已送达与否的二值判定,由各域策略器声明。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast
from urllib.parse import urlsplit


class RetryClass(Enum):
    """失败分类(策略器职责 4):OK=成功;UNDELIVERED=连接层未送达(重试至多
    一次);PROTOCOL=已送达但响应不合格(4xx/5xx/报文畸形/读超时,不重试)。"""

    OK = "ok"
    UNDELIVERED = "undelivered"
    PROTOCOL = "protocol"


# 域失败文案前缀单源(094 评审循环 D4:域执行器收敛文案与网关 _was_delivered
# 的送达判定共引,防多处字面量漂移;knowledge 域已随 081 缺口修复一并收编)
FAILURE_UNDELIVERED_PREFIX = "unreachable:"
FAILURE_PROTOCOL_PREFIX = "protocol:"


# 工具名 → 域标识单源(审计域标签与域配额闸共用;089 仅 knowledge 收编)。
# 放 models(零依赖数据),避免 toolcall.service 传递性引入 httpx(审查 P2)。
DOMAIN_TOOLS: dict[str, str] = {"knowledge_search": "knowledge", "web_search": "websearch"}


@dataclass(frozen=True)
class SearchProvider:
    """websearch 域检索供应商注册条目(纯数据;协议适配在 websearch.py)。"""

    endpoint: str
    host: str


# 094 websearch 域参数边界单源(注册表 schema 与域策略器/分发缺省共引;
# 审查 P2-3:防三点人工对齐漂移)
WEBSEARCH_QUERY_MAX_LEN = 500
WEBSEARCH_COUNT_MIN = 1
WEBSEARCH_COUNT_MAX = 10
WEBSEARCH_COUNT_DEFAULT = 5

# 094 websearch 域:首发供应商注册表(纯数据,config/registry 零 httpx 传递)。
# 端点由供应商单源派生,部署不可单独改端点(防白名单外端点的组合攻击面)。
WEBSEARCH_PROVIDERS: dict[str, SearchProvider] = {
    "tavily": SearchProvider(endpoint="https://api.tavily.com/search", host="api.tavily.com"),
    "bocha": SearchProvider(
        endpoint="https://api.bochaai.com/v1/web-search", host="api.bochaai.com"
    ),
    # 智谱独立检索 API(与 LLM 同平台,复用已有 bigmodel key 零额外注册;
    # 契约按官方 docs.bigmodel.cn「网络搜索」API 核对于 2026-10-11)
    "zhipu": SearchProvider(
        endpoint="https://open.bigmodel.cn/api/paas/v4/web_search", host="open.bigmodel.cn"
    ),
}


class DomainPolicy(Protocol):
    """域策略器协议(四职责,纯函数);各域实现结构性满足即可注册。"""

    def exit_host_allowed(self, host: str) -> bool: ...

    def validate_args(self, args: dict[str, object]) -> str | None: ...

    def redact(self, response: object) -> object: ...

    def classify(self, exc: Exception | None, *, delivered: bool) -> "RetryClass": ...


@dataclass(frozen=True)
class DomainSpec:
    """域清单条目(声明式数据,装载产物不可变)。"""

    id: str
    endpoint: str
    whitelist_hosts: tuple[str, ...]
    timeout_ms: int
    quota_per_minute: int
    redaction: str


def _endpoint_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def load_manifest(raw: list[dict[str, object]]) -> tuple[dict[str, DomainSpec], list[str]]:
    """域清单装载(纯函数,同配置同结果):缺必填字段/白名单主机非法/标识
    冲突的域剔除并告警,其余正常注册;不抛异常(fail-closed 不裸崩)。"""
    domains: dict[str, DomainSpec] = {}
    warnings: list[str] = []
    for entry in cast("list[object]", raw):
        entryd = cast("dict[str, object] | None", entry if isinstance(entry, dict) else None)
        if entryd is None:
            warnings.append("toolgateway: 清单条目非对象,已剔除")
            continue
        did = str(entryd.get("id") or "")
        endpoint = str(entryd.get("endpoint") or "")
        hosts_raw = entryd.get("whitelist_hosts")
        hosts: tuple[str, ...] = ()
        if isinstance(hosts_raw, list):
            host_list = cast("list[object]", hosts_raw)
            # 白名单主机非法条目剔除(spec:非字符串/空/含路径或通配不构成精确主机)
            hosts = tuple(
                h
                for h in (x.strip().lower() for x in host_list if isinstance(x, str))
                if h and "/" not in h and "*" not in h
            )
        timeout = entryd.get("timeout_ms")
        quota = entryd.get("quota_per_minute")
        redaction = entryd.get("redaction")
        problems: list[str] = []
        if not did:
            problems.append("缺域标识")
        if not endpoint:
            problems.append("缺端点")
        if not hosts:
            problems.append("缺出口白名单")
        if not _endpoint_host(endpoint):
            problems.append("端点主机非法")
        # bool 是 int 子类:true 穿透校验会得到 1ms 超时/1 次配额——显式排除
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            problems.append("超时非法")
        if not isinstance(quota, int) or isinstance(quota, bool) or quota <= 0:
            problems.append("配额非法")
        if did in domains:
            problems.append("标识冲突(后者剔除)")
        if problems:
            warnings.append(f"toolgateway: 域 {did or '(无名)'} 剔除——{'、'.join(problems)}")
            continue
        domains[did] = DomainSpec(
            id=did,
            endpoint=endpoint,
            whitelist_hosts=hosts,
            timeout_ms=timeout if isinstance(timeout, int) else 0,
            quota_per_minute=quota if isinstance(quota, int) else 0,
            redaction=str(redaction) if isinstance(redaction, str) else "allowlist",
        )
    return domains, warnings
