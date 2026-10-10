"""Agent 面配置。

ADR-002 纪律:本文件(以及整个 apps/agent)**不存在任何数据库配置项**。
数据获取的唯一途径 = 携带内部令牌回调控制面 /internal/*。
021:多模型注册表/路由表/出口白名单经 env JSON 注入;api_key 仅进程内存。
"""

import json
import logging
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

# 多协议出站(multi-protocol-llm,ADR-008):出站协议取值域单源。
# 缺省 openai = 引入前行为逐字回放;条目畸形值剔除告警,主模型畸形值回退 openai。
VALID_PROTOCOLS: tuple[str, ...] = ("openai", "anthropic", "responses")
DEFAULT_PROTOCOL = "openai"


@dataclass(frozen=True)
class ModelEntry:
    """注册表模型条目(021);api_key 仅进程内存,不落库不落日志
    (repr=False 加固:防未来日志误打整个条目,评审 L6)。"""

    name: str
    base_url: str
    api_key: str = field(repr=False)
    model: str = ""
    capabilities: list[str] = field(default_factory=list[str])
    max_image_mb: float | None = None  # 036⑦:图片体积上限(vision 模型;None=缺省)
    protocol: str = DEFAULT_PROTOCOL  # 出站协议(ADR-008):openai|anthropic|responses
    # 080:上下文窗口(装配预算输入;None=未声明→回退全局缺省)。进程内配置,
    # 不出现在任何响应/管理面输出(与 api_key 同级的私密性口径)
    context_window: int | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="KM_AGENT_", extra="ignore")

    app_name: str = "kongming-agent"
    log_level: str = "INFO"  # 061:运行日志级别

    # LLM(可切换:通义/DeepSeek/任何 openai 兼容端点)
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_api_key: str | None = None
    llm_timeout_sec: float = 30.0  # 单次 LLM 调用超时

    # embedding(009):独立于 LLM 主模型配置——上游 028 教训:主模型切非通义
    # 供应商时 embeddings API 不存在,RAG 会静默全量失败;缺省回退 llm_* 组,
    # dashscope 兼容端点常驻 embedding 能力。
    embedding_base_url: str | None = None  # None=回退 llm_base_url
    embedding_api_key: str | None = None  # None=回退 llm_api_key
    embedding_model: str = "text-embedding-v2"
    embedding_timeout_sec: float = 30.0
    embedding_max_texts: int = 64  # 单请求文本上限(防单请求打爆)
    embedding_batch_size: int = 25  # 供应商单批上限(dashscope v2=25;内部分批)

    # 控制面回调地址 + 内部令牌(注意:这不是用户凭证,轮换由控制面发起)
    api_base_url: str = "http://localhost:8000"
    api_timeout_sec: float = 30.0  # 单次回调控制面超时(execute/meta)
    internal_token: str | None = None

    # ReAct 内核(019):工具调用轮次上限(防无限循环;每轮=一次 LLM 决策+
    # 一批工具执行,超出即强制终止并返回结构化错误)
    max_tool_rounds: int = 8

    # 多智能体协作(091):部署级上限(与 api 侧拓扑钳制互为双保险;缺省
    # 与编排常量同值同源——worker 数/嵌套深度/critic 修订轮次)
    collab_max_workers: int = 3
    collab_max_depth: int = 1
    collab_max_revision_rounds: int = 1

    # 报告生成编排(088):报告级预算三约束(部署级可配,耗尽即停落 PARTIAL——
    # 「预算退出诚实」)与快照行数上限(与 api 刷新路径同量级)
    report_max_llm_calls: int = 30
    report_max_total_tokens: int = 200_000
    report_max_seconds: float = 300.0
    report_snapshot_rows: int = 500

    # 多模型(021):注册表/路由表/出口白名单(env JSON;空=单模型零回归)
    model_registry_json: str = ""
    model_routes_json: str = ""
    model_allowed_hosts: str = ""  # 逗号分隔;缺省=主模型 base_url 主机

    # SQL 安全引擎(053):builtin | shadow | primary;默认影子(内置权威,
    # 引擎只对拍观测)。validate 预检纯解析无 IO,引擎缺失/异常自动降级不阻断。
    sqlguard_mode: str = "shadow"

    # 036⑦:主模型图片体积上限(MB;注册表条目 max_image_mb 可覆盖)
    llm_max_image_mb: float = 10.0

    # 多协议出站(ADR-008):主模型出站协议;非法值启动期告警回退 openai
    llm_protocol: str = DEFAULT_PROTOCOL

    # 生成提示词装配预算(080):窗口缺省值 + 预留输出 + 安全边际;
    # 预算 = 窗口 − 预留输出 − 安全边际(条目级 context_window 优先)
    prompt_context_window_default: int = 32768
    prompt_reserved_output_tokens: int = 4096
    prompt_safety_margin_tokens: int = 1024

    # 表级预筛(063):问题+资产驱动的表结构收敛;关闭=全量注入(基线回退)
    schema_prune: bool = True
    schema_prune_top_n: int = 12

    # 三态意图路由(093):LLM 判定超时与置信阈值——超时/失败/低置信即回退
    # 既有规则词表(词表无融合态,回退态天然保守,最坏退回 093 前行为)
    intent_route_timeout_sec: float = 3.0
    intent_route_min_confidence: float = 0.7

    # 出站韧性(072):瞬时故障重试与单层降级。attempt 预算按上层
    # agent_timeout_sec=120 反推(design D4:最坏 2×30s + 退避 + 1×30s)
    llm_max_attempts: int = 3  # 单次业务调用总尝试上限(含降级目标)
    llm_retry_base_ms: int = 500  # 退避基数(指数+全量 jitter)
    llm_retry_max_wait_ms: int = 5000  # 退避上限(Retry-After 亦截断到此)
    # 降级条目:主模型也瞬时故障耗尽后降级到该注册表条目;空=关闭(design D3)
    llm_fallback_entry: str = ""

    def parse_registry(self) -> dict[str, ModelEntry]:
        """注册表解析(fail-closed):畸形条目告警剔除;重名拒;白名单外拒配。"""
        if not self.model_registry_json.strip():
            return {}
        try:
            raw = json.loads(self.model_registry_json)
        except json.JSONDecodeError as e:
            log.error("MODEL_REGISTRY_JSON 解析失败,回退单模型: %s", e)
            return {}
        if not isinstance(raw, list):
            log.error("MODEL_REGISTRY_JSON 须为数组,回退单模型")
            return {}
        allowed = self.allowed_hosts()
        out: dict[str, ModelEntry] = {}
        # env JSON 边界:isinstance 守卫后收窄键型,下游 get 全部可推断
        for i, raw_item in enumerate(cast("list[object]", raw)):
            if not isinstance(raw_item, dict):
                log.warning("注册表条目 %d 非对象,剔除", i)
                continue
            item = cast("dict[str, object]", raw_item)
            name = str(item.get("name") or "")
            base_url = str(item.get("base_url") or "")
            api_key = str(item.get("api_key") or "")
            model = str(item.get("model") or name)
            if not name or not base_url or not api_key:
                log.warning("注册表条目 %d 缺 name/base_url/api_key,剔除", i)
                continue
            if name in out:
                log.warning("注册表条目重名 %s,剔除", name)
                continue
            host = urlparse(base_url).hostname or ""
            if host not in allowed:
                log.warning("注册表条目 %s 主机 %s 不在白名单,拒配", name, host)
                continue
            # 075:计价字段已移除(零读点死配置);未知键静默忽略,不拒配
            # 036⑦:图片上限(畸形剔除该字段)
            max_image_mb: float | None = None
            raw_mimb = cast("int | float | str | None", item.get("max_image_mb"))
            if raw_mimb is not None:
                try:
                    max_image_mb = float(raw_mimb)
                    if max_image_mb <= 0:
                        raise ValueError("非正数")
                except (TypeError, ValueError):
                    log.warning("注册表条目 %s max_image_mb 畸形,剔除该字段", name)
                    max_image_mb = None
            # 多协议(ADR-008):protocol 取值域外 = 与缺字段同级,单条目剔除告警
            protocol = str(item.get("protocol") or DEFAULT_PROTOCOL)
            if protocol not in VALID_PROTOCOLS:
                log.warning("注册表条目 %s protocol 非法: %s,剔除该条目", name, protocol)
                continue
            # 080:上下文窗口——畸形(非正数/不可解析)按未声明处理并告警,
            # SHALL NOT 拒配整条目(窗口只用于预算收敛,不构成安全边界)
            context_window: int | None = None
            raw_cw = item.get("context_window")
            if raw_cw is not None:
                try:
                    cw = int(cast("int | float | str", raw_cw))
                    if cw <= 0:
                        raise ValueError("非正数")
                    context_window = cw
                except (TypeError, ValueError):
                    log.warning("注册表条目 %s context_window 畸形,按未声明处理", name)
            out[name] = ModelEntry(
                name=name,
                base_url=base_url,
                api_key=api_key,
                model=model or name,
                capabilities=[str(c) for c in cast("list[object]", item.get("capabilities") or [])],
                max_image_mb=max_image_mb,
                protocol=protocol,
                context_window=context_window,
            )
        return out

    def parse_routes(self) -> dict[str, str]:
        """路由表解析:task → 模型名;畸形回退空表(全走主模型)。"""
        if not self.model_routes_json.strip():
            return {}
        try:
            raw = json.loads(self.model_routes_json)
        except json.JSONDecodeError as e:
            log.error("MODEL_ROUTES_JSON 解析失败,全走主模型: %s", e)
            return {}
        if not isinstance(raw, dict):
            return {}
        # isinstance 收窄产物 dict[Unknown, Unknown] 仍报,守卫后收窄键型
        typed = cast("dict[str, object]", raw)
        return {str(k): str(v) for k, v in typed.items()}

    def allowed_hosts(self) -> set[str]:
        """出口白名单:显式配置 + 主模型/embedding 供应商主机(缺省兼容)。"""
        hosts = {h.strip() for h in self.model_allowed_hosts.split(",") if h.strip()}
        for url in (self.llm_base_url, self.embedding_base_url or ""):
            if url:
                host = urlparse(url).hostname
                if host:
                    hosts.add(host)
        return hosts

    def fail_closed_checks(self) -> None:
        import os

        # 防御性断言:环境里不得出现 DB 凭证痕迹(ADR-002 审计口径)
        suspicious = [k for k in os.environ if "DATABASE_URL" in k or "DB_PASSWORD" in k]
        if suspicious:
            raise RuntimeError(f"agent 进程检测到数据库凭证环境变量: {suspicious}")
        if self.llm_api_key is None:
            import warnings

            warnings.warn("KM_AGENT_LLM_API_KEY 未配置,LLM 调用将失败", stacklevel=2)
        # 021(R1-M1):启动期解析注册表/路由表并交叉校验——畸形配置启动即暴露,
        # 路由目标不在注册表(且非主模型)逐条告警(配置漂移可发现)
        registry = self.parse_registry()
        for task, name in self.parse_routes().items():
            if name not in registry:
                log.warning("路由目标不在注册表,将静默回退主模型: task=%s target=%s", task, name)
        # 多协议(ADR-008):主模型 protocol 非法 = 告警回退 openai,不拒启
        # (与「主模型 base_url 不在白名单只告警不拒」的既有兼容口径一致)
        if self.llm_protocol not in VALID_PROTOCOLS:
            log.warning("KM_AGENT_LLM_PROTOCOL 非法: %s,回退 openai", self.llm_protocol)
            self.llm_protocol = DEFAULT_PROTOCOL
        # 072:降级条目启动期校验——不在注册表(或条目被剔除)则告警并视为
        # 未配置(降级链关闭,主模型自身重试照常);不拒启
        fallback = (self.llm_fallback_entry or "").strip()
        if fallback and fallback not in registry:
            log.warning("KM_AGENT_LLM_FALLBACK_ENTRY %s 不在注册表,视为未配置(降级关闭)", fallback)
