"""LLM 客户端(openai 兼容协议,通义/DeepSeek 改环境变量即切换,nl2sql spec)。

ADR-002 注记:LLM key 是 agent 面唯一持有的外部凭证(非数据凭证)。
纪律:同步 request/response,无自写重试——selfheal 是链路唯一重试机制。
"""

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from kongming_agent.settings import Settings


class LlmError(Exception):
    """LLM 调用失败(key 缺失/网络/超时/协议异常)的统一出口。"""


class LlmClient:
    """薄封装:chat 文本问答 + embeddings;温度由调用方按节点语义给定。"""

    def __init__(self, settings: Settings) -> None:
        self._model = settings.llm_model
        self._client: AsyncOpenAI | None = None
        if settings.llm_api_key:
            self._client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_sec,
            )
        # embedding 独立客户端(009):主模型与 embedding 供应商解耦(上游 028 教训)
        emb_key = settings.embedding_api_key or settings.llm_api_key
        emb_base = settings.embedding_base_url or settings.llm_base_url
        self._emb_model = settings.embedding_model
        self._emb_max_texts = settings.embedding_max_texts
        self._emb_batch_size = settings.embedding_batch_size
        self._emb_client: AsyncOpenAI | None = None
        if emb_key:
            self._emb_client = AsyncOpenAI(
                api_key=emb_key,
                base_url=emb_base,
                timeout=settings.embedding_timeout_sec,
            )

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        if self._client is None:
            raise LlmError("KM_AGENT_LLM_API_KEY 未配置")
        # max_tokens 缺省不传(供应商默认),仅轻量端点(如标题改写)显式封顶(010);
        # 分支写法:openai 类型 None ≠ 省略,**kwargs 过不了 mypy 重载
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            if max_tokens is None:
                resp = await self._client.chat.completions.create(
                    model=self._model, messages=messages, temperature=temperature
                )
            else:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
        except Exception as e:
            raise LlmError(f"LLM 调用失败: {e}") from e
        return (resp.choices[0].message.content or "").strip()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化(009;openai 兼容 embeddings API)。

        供应商单批条数受限(dashscope text-embedding-v2=25,冒烟实证 28 条整批被拒),
        内部按 embedding_batch_size 切分多次调用后拼接。
        """
        if self._emb_client is None:
            raise LlmError("embedding/LLM API key 均未配置")
        if not texts or len(texts) > self._emb_max_texts:
            raise LlmError(f"embeddings 批量须为 1..{self._emb_max_texts} 条")
        out: list[list[float]] = []
        for start in range(0, len(texts), self._emb_batch_size):
            batch = texts[start : start + self._emb_batch_size]
            try:
                resp = await self._emb_client.embeddings.create(
                    model=self._emb_model, input=batch
                )
            except Exception as e:
                raise LlmError(f"embeddings 调用失败: {e}") from e
            # 按 index 归位(API 不保证返回有序)
            out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        return out
