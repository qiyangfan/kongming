"""评估器共用的 OpenAI 兼容客户端，提供对话和向量化接口。"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import asyncio
import json
import os
from typing import Any, cast

import httpx

_DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL = "qwen-plus"
_DEFAULT_EMBEDDING_MODEL = "text-embedding-v2"
_TIMEOUT = 120.0  # judge 单次评审可较长(与生成侧长答同量级)
_EMBED_TIMEOUT = 60.0
_EMBED_BATCH = 25  # dashscope text-embedding-v2 单批上限(agent 面同值)


class JudgeLlmError(RuntimeError):
    """judge/构造 LLM 侧失败(凭证缺失/调用失败/应答畸形)。"""


def llm_api_key() -> str:
    key = os.environ.get("KM_EVAL_LLM_API_KEY") or os.environ.get("KM_AGENT_LLM_API_KEY") or ""
    if not key:
        raise JudgeLlmError(
            "judge LLM 凭证缺失(KM_EVAL_LLM_API_KEY / KM_AGENT_LLM_API_KEY)——显式失败,不静默跳过"
        )
    return key


class JudgeLlm:
    """judge 与自动构造共用的最小 openai 兼容客户端(chat + embed)。"""

    def __init__(self) -> None:
        self._base = (os.environ.get("KM_EVAL_LLM_BASE_URL") or _DEFAULT_BASE).rstrip("/")
        self._headers = {"Authorization": f"Bearer {llm_api_key()}"}
        self._model = (
            os.environ.get("KM_EVAL_LLM_MODEL")
            or os.environ.get("KM_AGENT_LLM_MODEL")
            or _DEFAULT_MODEL
        )
        self._embedding_model = (
            os.environ.get("KM_EVAL_EMBEDDING_MODEL") or _DEFAULT_EMBEDDING_MODEL
        )

    async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
        content, _ = await self.chat_with_usage(system, user, max_tokens=max_tokens)
        return content

    async def chat_with_usage(
        self, system: str, user: str, *, max_tokens: int = 1200
    ) -> tuple[str, dict[str, Any]]:
        """对话并返回 (content, usage);服务端未给 usage 时为空映射(报告按未计入)。"""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            last_exc: httpx.HTTPError | None = None
            for attempt in range(2):  # 连接层抖动(网关/本机断连)重试一次;业务错误不重试
                try:
                    resp = await client.post(
                        f"{self._base}/chat/completions", json=payload, headers=self._headers
                    )
                    resp.raise_for_status()
                    break
                except httpx.TransportError as e:
                    last_exc = e
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                except httpx.HTTPError as e:
                    raise JudgeLlmError(f"judge LLM 调用失败: {e}") from e
            else:
                raise JudgeLlmError(f"judge LLM 调用失败: {last_exc}") from last_exc
        # 应答体解析同样收口 JudgeLlmError(非 JSON/异形应答是「LLM 侧失败」
        # 而非程序缺陷;runner 只接 JudgeLlmError/JudgeParseError——裸异常
        # 逃逸会把单对象失败放大成整个 run 中断,破坏独立收口契约)
        body = _json_or_error(resp, "judge LLM")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise JudgeLlmError("judge LLM 应答畸形(空 choices)")
        message = cast("dict[str, object]", choices[0]).get("message")
        if not isinstance(message, dict):
            raise JudgeLlmError("judge LLM 应答畸形(message 缺失)")
        content = str(message.get("content") or "").strip()
        usage = body.get("usage")
        return content, cast("dict[str, Any]", usage) if isinstance(usage, dict) else {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化(内部分批;index 集合校验同 agent 面 embeddings 的校验口径)。"""
        if not texts:
            return []
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
            for start in range(0, len(texts), _EMBED_BATCH):
                batch = texts[start : start + _EMBED_BATCH]
                try:
                    resp = await client.post(
                        f"{self._base}/embeddings",
                        json={"model": self._embedding_model, "input": batch},
                        headers=self._headers,
                    )
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    raise JudgeLlmError(f"embedding 调用失败: {e}") from e
                body = _json_or_error(resp, "embedding")
                data_raw = body.get("data")
                if not isinstance(data_raw, list):
                    raise JudgeLlmError("embedding 应答畸形(data 非数组)")
                data = sorted(
                    [cast("dict[str, object]", d) for d in data_raw if isinstance(d, dict)],
                    key=lambda d: cast("int", d.get("index", 0)),
                )
                if [cast("int", d.get("index", -1)) for d in data] != list(range(len(batch))):
                    raise JudgeLlmError("embedding 应答畸形:index 集合不完整")
                for d in data:
                    emb = d.get("embedding")
                    if not isinstance(emb, list):
                        raise JudgeLlmError("embedding 应答畸形:条目缺 embedding 数组")
                    try:
                        out.append([float(x) for x in emb])
                    except (TypeError, ValueError) as e:
                        raise JudgeLlmError("embedding 应答畸形:向量含非数值") from e
        return out


def _json_or_error(resp: httpx.Response, label: str) -> dict[str, object]:
    """应答 JSON 解析单点(非 JSON → JudgeLlmError;调用方标签进错误文案)。"""
    try:
        parsed: object = resp.json()
    except ValueError as e:  # json.JSONDecodeError 等
        raise JudgeLlmError(f"{label} 应答非 JSON") from e
    if not isinstance(parsed, dict):
        raise JudgeLlmError(f"{label} 应答非 JSON 对象")
    return cast("dict[str, object]", parsed)


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """容错 JSON 解析(剥 markdown 标记/取首尾花括号;不可解析返回 None)。"""
    text = raw.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None
