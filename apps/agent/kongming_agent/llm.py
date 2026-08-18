"""LLM 客户端(openai 兼容协议,通义/DeepSeek 改环境变量即切换,nl2sql spec)。

ADR-002 注记:LLM key 是 agent 面唯一持有的外部凭证(非数据凭证)。
纪律:同步 request/response,无自写重试——selfheal 是链路唯一重试机制。
"""

from openai import AsyncOpenAI

from kongming_agent.settings import Settings


class LlmError(Exception):
    """LLM 调用失败(key 缺失/网络/超时/协议异常)的统一出口。"""


class LlmClient:
    """薄封装:chat 文本问答;温度由调用方按节点语义给定。"""

    def __init__(self, settings: Settings) -> None:
        self._model = settings.llm_model
        self._client: AsyncOpenAI | None = None
        if settings.llm_api_key:
            self._client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_sec,
            )

    async def chat(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        if self._client is None:
            raise LlmError("KM_AGENT_LLM_API_KEY 未配置")
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
        except Exception as e:
            raise LlmError(f"LLM 调用失败: {e}") from e
        return (resp.choices[0].message.content or "").strip()
