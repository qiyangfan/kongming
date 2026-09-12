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


@dataclass(frozen=True)
class ModelEntry:
    """注册表模型条目(021);api_key 仅进程内存,不落库不落日志
    (repr=False 加固:防未来日志误打整个条目,评审 L6)。"""

    name: str
    base_url: str
    api_key: str = field(repr=False)
    model: str = ""
    capabilities: list[str] = field(default_factory=list[str])
    price_input: float | None = None  # 每百万 tokens 单价(可选)
    price_output: float | None = None
    max_image_mb: float | None = None  # 036⑦:图片体积上限(vision 模型;None=缺省)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="KM_AGENT_", extra="ignore")

    app_name: str = "kongming-agent"

    # LLM(可切换:通义/DeepSeek/任何 openai 兼容端点)
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_api_key: str | None = None
    llm_timeout_sec: float = 30.0  # 单次 LLM 调用超时

    # embedding(009):独立于 LLM 主模型配置——上游 028 教训:主模型切非通义
    # 供应商时 embeddings API 不存在,RAG 会静默全量失败;缺省回退 llm_* 组,
    # dashscope 兼容端点常驻 embedding 能力。
    embedding_base_url: str | None = None  # None=回退 llm_base_url
    embedding_api_key: str | None = None   # None=回退 llm_api_key
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

    # 多模型(021):注册表/路由表/出口白名单(env JSON;空=单模型零回归)
    model_registry_json: str = ""
    model_routes_json: str = ""
    model_allowed_hosts: str = ""  # 逗号分隔;缺省=主模型 base_url 主机

    # SQL 安全引擎(053):builtin | shadow | primary;默认影子(内置权威,
    # 引擎只对拍观测)。validate 预检纯解析无 IO,引擎缺失/异常自动降级不阻断。
    sqlguard_mode: str = "shadow"

    # 036⑦:主模型图片体积上限(MB;注册表条目 max_image_mb 可覆盖)
    llm_max_image_mb: float = 10.0

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
            # 计价字段类型校验(R1-L7:字符串等脏数据剔除,不落 dataclass)
            price_in = cast("int | float | str | None", item.get("price_input"))
            price_out = cast("int | float | str | None", item.get("price_output"))
            try:
                price_in = float(price_in) if price_in is not None else None
                price_out = float(price_out) if price_out is not None else None
            except (TypeError, ValueError):
                log.warning("注册表条目 %s 计价畸形,剔除计价字段", name)
                price_in = price_out = None
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
            out[name] = ModelEntry(
                name=name,
                base_url=base_url,
                api_key=api_key,
                model=model or name,
                capabilities=[str(c) for c in cast("list[object]", item.get("capabilities") or [])],
                price_input=price_in,
                price_output=price_out,
                max_image_mb=max_image_mb,
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
                log.warning(
                    "路由目标不在注册表,将静默回退主模型: task=%s target=%s", task, name
                )
