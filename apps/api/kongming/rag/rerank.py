"""文档精排的出站适配器。

只有 `KM_RERANK_*` 配置齐全且供应商通过白名单校验时才启用；调用失败
统一降级到原检索结果。用量写入 `sys_llm_usage`。凭证只用于请求头，
不写入日志、审计记录或错误文本。"""

import json
import logging
from collections.abc import Callable
from typing import Protocol, cast
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text

from kongming.config import RERANK_PROVIDERS, Settings, get_settings
from kongming.db import get_sessionmaker
from kongming.trace import get_trace_id
from kongming.uuid7 import uuid7

log = logging.getLogger(__name__)

# 响应读取上限(字节闸,防超大响应内存放大)
_RESPONSE_CAP = 1024 * 1024

# 降级原因三类(可观测 reason 单源;usage status 恒 ERROR + 日志带 reason)
REASON_TIMEOUT = "timeout"
REASON_RATELIMIT = "ratelimit"
REASON_UNAVAILABLE = "unavailable"

_USAGE_TASK = "doc_rerank"


class RerankError(Exception):
    """精排出站失败的结构化收敛(reason ∈ 三类;文本不含凭证材料)。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"rerank {reason}: {detail}")
        self.reason = reason


class RerankPort(Protocol):
    """retrieve_docs 消费的精排端口(结构化类型;实现=DocReranker/测试假体)。

    model/pool_topk/score_threshold 随端口下发:候选池扩量与阈值语义归
    精排级自身,检索编排不读部署配置。"""

    model: str
    pool_topk: int
    score_threshold: float

    async def score(self, *, query: str, documents: list[str]) -> list[float]:
        """对候选池打分;返回与 documents 顺序对齐的分数(缺失位记 0.0)。"""
        ...


# ---- 两段式适配器:请求装配(统一入参→供应商方言)/响应归一(报文→{index: score}) ----


def _siliconflow_request(model: str, query: str, documents: list[str]) -> dict[str, object]:
    return {"model": model, "query": query, "documents": documents}


def _bailian_request(model: str, query: str, documents: list[str]) -> dict[str, object]:
    """百炼原生端点(gte-rerank 系必须走原生,/compatible-mode 仅 qwen3-rerank)。

    parameters.return_documents=False:只要 index/score,不回传原文。"""
    return {
        "model": model,
        "input": {"query": query, "documents": documents},
        "parameters": {"return_documents": False},
    }


def _as_dict(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _require_body(value: object) -> dict[str, object]:
    """响应体必须是 JSON 对象;畸形按协议失败上抛(不静默降级为空重排)。"""
    body = _as_dict(value)
    if body is None:
        raise RerankError(REASON_UNAVAILABLE, "响应报文畸形(JSON 对象缺失)")
    return body


def _normalize_results(raw: object) -> dict[int, float]:
    """Cohere 形态 results[].{index, relevance_score} → {index: score}。

    畸形/缺字段项跳过(对应位按 0.0 对齐);results 缺失=不可用。"""
    if not isinstance(raw, list):
        raise RerankError(REASON_UNAVAILABLE, "响应缺 results 列表")
    out: dict[int, float] = {}
    for item in cast("list[object]", raw):
        d = _as_dict(item)
        if d is None:
            continue
        idx = d.get("index")
        score = d.get("relevance_score")
        if (
            isinstance(idx, int)
            and not isinstance(idx, bool)
            and isinstance(score, (int, float))
            and not isinstance(score, bool)
        ):
            out[idx] = float(score)
    return out


def _normalize_siliconflow(body: dict[str, object]) -> dict[int, float]:
    return _normalize_results(body.get("results"))


def _normalize_bailian(body: dict[str, object]) -> dict[int, float]:
    """output.results(原生形态);顶层 results 兜底(qwen3-rerank 起新形态)。

    双形态都缺 results 列表时由 _normalize_results 报不可用。"""
    output = _as_dict(body.get("output"))
    if isinstance(output, dict) and isinstance(output.get("results"), list):
        return _normalize_results(output["results"])
    return _normalize_results(body.get("results"))


_Adapter = tuple[
    Callable[[str, str, list[str]], dict[str, object]],
    Callable[[dict[str, object]], dict[int, float]],
]
_ADAPTERS: dict[str, _Adapter] = {
    "siliconflow": (_siliconflow_request, _normalize_siliconflow),
    "bailian": (_bailian_request, _normalize_bailian),
}


class _ResponseTooLarge(Exception):
    """响应超字节闸(内部信号;执行器收敛为 unavailable)。"""


async def _read_json_capped(resp: httpx.Response) -> object:
    """stream 上下文内读 JSON 响应(字节闸内)——
    resp.json 会全量预读入内存。"""
    buf = bytearray()
    async for chunk in resp.aiter_bytes(65536):
        buf.extend(chunk)
        if len(buf) > _RESPONSE_CAP:
            raise _ResponseTooLarge from None
    return json.loads(buf.decode("utf-8", errors="replace"))


class DocReranker:
    """精排执行器:单次供应商协议适配(POST + Bearer);零治理。

    失败在执行器内收敛为 RerankError(三类 reason);降级决策与用量留痕由
    检索编排(documents_service)承担。transport 仅测试注入;凭证只在
    构造期注入,只进请求头。"""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        timeout_s: float,
        pool_topk: int,
        score_threshold: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if provider not in RERANK_PROVIDERS:  # 组合根直构未过白名单=装配缺陷显式化
            raise ValueError(f"rerank 未知供应商:{provider}")
        if provider not in _ADAPTERS:  # 注册表与适配器表共维(缺一启动即显式报错)
            raise ValueError(f"rerank 供应商缺适配器:{provider}")
        self._provider = provider
        self._endpoint, self._host = RERANK_PROVIDERS[provider]
        self._api_key = api_key
        self._request, self._normalize = _ADAPTERS[provider]
        self._timeout_s = timeout_s
        self._transport = transport
        self.model = model
        self.pool_topk = pool_topk
        self.score_threshold = score_threshold

    def __repr__(self) -> str:  # 凭证不进 repr
        return (
            f"DocReranker(provider={self._provider} model={self.model} "
            f"pool_topk={self.pool_topk} threshold={self.score_threshold})"
        )

    async def score(self, *, query: str, documents: list[str]) -> list[float]:
        """单次出站打分;返回与 documents 对齐的分数(供应商未返回的位记 0.0)。"""
        # 未完整取得响应族(拒连在 macOS anyio 后端以 ReadError 浮现,按语义归纳)
        undelivered = (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.CloseError,
        )
        try:
            async with (
                httpx.AsyncClient(
                    trust_env=False,  # 出站受控:不采环境代理(SSRF 钉扎/内网回调语义)
                    timeout=httpx.Timeout(self._timeout_s),
                    transport=self._transport,
                ) as client,
                # stream 上下文+字节闸读取
                client.stream(
                    "POST",
                    self._endpoint,
                    json=self._request(self.model, query, documents),
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ) as resp,
            ):
                resp.raise_for_status()
                indexed = self._normalize(_require_body(await _read_json_capped(resp)))
        except undelivered as e:
            raise RerankError(REASON_UNAVAILABLE, f"拒连({type(e).__name__})") from e
        except RerankError:
            raise
        except httpx.TimeoutException as e:
            # 读超时=请求已送达、响应未按期归一为 timeout
            raise RerankError(REASON_TIMEOUT, f"读超时({type(e).__name__})") from e
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            reason = REASON_RATELIMIT if code == 429 else REASON_UNAVAILABLE
            raise RerankError(reason, f"上游 HTTP {code}") from e
        except _ResponseTooLarge as e:
            raise RerankError(REASON_UNAVAILABLE, f"响应超 {_RESPONSE_CAP // 1024}KB 上限") from e
        except (ValueError, TypeError) as e:
            raise RerankError(REASON_UNAVAILABLE, f"响应报文畸形({type(e).__name__})") from e
        except httpx.DecodingError as e:
            # 解码失败不单列会以裸 httpx 异常逃逸收敛链
            raise RerankError(REASON_UNAVAILABLE, f"响应解码失败({type(e).__name__})") from e
        except httpx.TransportError as e:
            # 传输层未枚举残族(防 httpx 分层演进后异常逃逸收敛链)
            raise RerankError(REASON_UNAVAILABLE, f"传输失败({type(e).__name__})") from e
        return [indexed.get(i, 0.0) for i in range(len(documents))]


def build_doc_reranker(settings: Settings | None = None) -> DocReranker | None:
    """按 KM_RERANK_* 配置创建精排器；配置不完整或 provider 不在白名单时返回 None。

    非法 provider 在启动时拒绝；无效出口配置会被剔除并记录告警。
    """
    s = settings or get_settings()
    provider = (s.rerank_provider or "").strip().lower()
    key = (s.rerank_api_key or "").strip()
    model = (s.rerank_model or "").strip()
    if not provider or not key or not model:
        return None
    if provider not in RERANK_PROVIDERS:
        return None
    endpoint, host = RERANK_PROVIDERS[provider]
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        log.warning(
            "rerank 供应商出口主机白名单校验未过,条目剔除,精排级不生效:%s",
            host or endpoint,
        )
        return None
    # 适配器缺位属装配缺陷(注册表与适配器表共维),交 DocReranker 显式 raise
    return DocReranker(
        provider=provider,
        model=model,
        api_key=key,
        timeout_s=s.rerank_timeout_ms / 1000,
        pool_topk=s.rerank_pool_topk,
        score_threshold=s.rerank_score_threshold,
    )


async def record_rerank_usage(
    *, model: str, tenant_id: str | None, latency_ms: int, status: str
) -> None:
    """rerank 出站留痕(sys_llm_usage;独立短事务,失败只告警不阻断)。

    task 固定 doc_rerank;rerank 无 completion 概念,token 三列记 0;
    trace_id 取请求级单源;凭证不落任何列。"""
    try:
        async with get_sessionmaker()() as s:
            await s.execute(
                text(
                    "INSERT INTO public.sys_llm_usage"
                    " (id, trace_id, tenant_id, model, task, prompt_tokens,"
                    "  completion_tokens, total_tokens, latency_ms, status)"
                    " VALUES (:id, :trace_id, CAST(:tenant_id AS uuid), :model,"
                    "         :task, 0, 0, 0, :latency_ms, :status)"
                ),
                {
                    "id": str(uuid7()),
                    "trace_id": get_trace_id(),
                    "tenant_id": tenant_id,
                    "model": model,
                    "task": _USAGE_TASK,
                    "latency_ms": latency_ms,
                    "status": status,
                },
            )
            await s.commit()
    except Exception:  # 用量记录失败只告警，不影响检索结果。
        log.warning("rerank 用量写入失败(不阻断)", exc_info=True)
