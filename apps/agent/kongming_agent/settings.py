"""Agent 面配置。

ADR-002 纪律:本文件(以及整个 apps/agent)**不存在任何数据库配置项**。
数据获取的唯一途径 = 携带内部令牌回调控制面 /internal/*。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    def fail_closed_checks(self) -> None:
        import os

        # 防御性断言:环境里不得出现 DB 凭证痕迹(ADR-002 审计口径)
        suspicious = [k for k in os.environ if "DATABASE_URL" in k or "DB_PASSWORD" in k]
        if suspicious:
            raise RuntimeError(f"agent 进程检测到数据库凭证环境变量: {suspicious}")
        if self.llm_api_key is None:
            import warnings

            warnings.warn("KM_AGENT_LLM_API_KEY 未配置,LLM 调用将失败", stacklevel=2)
