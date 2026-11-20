"""文档实体抽取(api 面 openai 兼容 chat 出站;部署级注册,缺省关)。

SAG 对照借鉴的单库化形态:实体=轻量词法索引(非语义载体),按父块批量
抽取、子块粒度落库;抽取失败降级为该文档无实体(与能力未开启不可区分),
每次出站留痕 sys_llm_usage(任务 doc_entity,凭证不落任何列)。
出口纪律与精排级同源:https 端点、私网/本机需显式豁免、不采环境代理。
"""

import json
import logging
import re
import time
import unicodedata
from typing import Protocol, cast
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text

from kongming.config import Settings, get_settings
from kongming.db import get_sessionmaker, uuid7
from kongming.trace import get_trace_id

log = logging.getLogger(__name__)

_REASON_UNAVAILABLE = "unavailable"
_REASON_TIMEOUT = "timeout"
_REASON_RATELIMIT = "ratelimit"

_SYSTEM_PROMPT = (
    "你是文档实体抽取器。给定一个父块文本与其切分出的子块列表,"
    "为每个子块抽取实体:专有名词、别名、关键业务概念(如等级名、费用名、"
    "流程名、规则名)。要求:\n"
    "1. 只抽确定性指称,不抽泛词(如 系统、用户、内容、信息、规则)\n"
    "2. 实体名使用原文写法,每子块最多 8 个\n"
    '3. 输出严格 JSON:{"entities": [[子块1的实体...], [子块2的实体...]]},'
    "数组与子块一一对应,不输出任何其他文本"
)

# 响应族未完整取得(拒连在 macOS anyio 后端以 ReadError 浮现,按语义归纳)
_UNDELIVERED = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.CloseError,
)

# 响应字节闸(父块批量抽取的 JSON 量级远小于此)
_EXTRACT_RESPONSE_CAP = 1024 * 1024


class EntityExtractError(Exception):
    """实体抽取服务不可用/超时/报文畸形——调用方按降级处理(无实体)。"""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def normalize_entity_name(name: str) -> str:
    """实体规范化 join 键:NFKC + casefold + 空白折叠(纯函数)。"""
    collapsed = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name)).strip()
    return collapsed.casefold()


class EntityExtractorPort(Protocol):
    """实体抽取窄端口(父块批量抽取 + 问句实体识别)。

    实现=DocEntityExtractor(api 面 openai 兼容出站);检索/上传编排与
    测试脚本假体都按端口消费(端口纪律,同 RerankPort 形态)。"""

    async def extract(self, parent_text: str, child_texts: list[str]) -> list[list[str]]: ...

    async def query_entities(self, question: str, *, max_entities: int = 8) -> list[str]: ...


def _strip_code_fence(body: str) -> str:
    """剥离 LLM 响应常见的代码栅栏包裹(容忍不完整栅栏形态)。"""
    body = body.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    return body


def _json_dict(raw: str, detail: str) -> dict[str, object]:
    """响应 JSON 装载并校验顶层对象(代码栅栏容忍;strict 装载产物显式定型)。"""
    try:
        data = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError) as e:
        raise EntityExtractError(_REASON_UNAVAILABLE, f"响应报文畸形({type(e).__name__})") from e
    if not isinstance(data, dict):
        raise EntityExtractError(_REASON_UNAVAILABLE, detail)
    return cast("dict[str, object]", data)


def _obj_list(value: object, detail: str) -> list[object]:
    """object → list[object] 校验窄化(非数组即畸形)。"""
    if not isinstance(value, list):
        raise EntityExtractError(_REASON_UNAVAILABLE, detail)
    return cast("list[object]", value)


def _parse_chat_content(raw: str) -> str:
    """chat 响应取首条 message.content;结构与内容缺失均按畸形上抛。"""
    payload = _json_dict(raw, "响应报文畸形(对象缺失)")
    choices = _obj_list(payload.get("choices"), "响应缺 choices")
    if not choices or not isinstance(choices[0], dict):
        raise EntityExtractError(_REASON_UNAVAILABLE, "响应缺 choices")
    message = cast("dict[str, object]", choices[0]).get("message")
    message_dict = cast("dict[str, object]", message) if isinstance(message, dict) else None
    content = message_dict.get("content") if message_dict is not None else None
    if not isinstance(content, str):
        raise EntityExtractError(_REASON_UNAVAILABLE, "响应缺 message.content")
    return content


def _parse_entities(raw: str, child_count: int) -> list[list[str]]:
    """父块批量抽取响应解析:返回与子块一一对应的实体名列表(已规范化去重)。

    长度不符按子块数截断/补空(抽多不惩罚);格子非数组按空处理。"""
    data = _json_dict(raw, "响应报文畸形(对象缺失)")
    entities = _obj_list(data.get("entities"), "响应缺 entities 数组")
    out: list[list[str]] = []
    for i in range(child_count):
        cell: object = entities[i] if i < len(entities) else []
        cell_list = cast("list[object]", cell) if isinstance(cell, list) else []
        names = [str(x) for x in cell_list]
        normalized = {normalize_entity_name(n) for n in names if normalize_entity_name(n)}
        out.append(sorted(normalized))
    return out


class DocEntityExtractor:
    """openai 兼容 chat 实体抽取器(凭证仅进程内,repr 不回显)。

    失败在执行器内收敛为 EntityExtractError(三类 reason);降级决策与
    落库编排由消费方(documents_service)承担。transport 仅测试注入。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._transport = transport

    def __repr__(self) -> str:  # 凭证不进 repr
        return f"DocEntityExtractor(model={self.model})"

    async def _post_chat(self, system: str, user_content: str) -> str:
        """单次流式 POST 并按字节闸读满响应正文(不解析)。"""
        async with (
            httpx.AsyncClient(
                trust_env=False,  # 出站受控:不采环境代理(与精排级同源)
                timeout=httpx.Timeout(self._timeout_s),
                transport=self._transport,
            ) as client,
            client.stream(
                "POST",
                self._endpoint,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0,
                },
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as resp,
        ):
            resp.raise_for_status()
            buf = bytearray()
            async for chunk in resp.aiter_bytes(65536):
                buf.extend(chunk)
                if len(buf) > _EXTRACT_RESPONSE_CAP:
                    raise EntityExtractError(_REASON_UNAVAILABLE, "响应超字节闸")
            return buf.decode("utf-8", errors="replace")

    async def _chat(self, system: str, user_content: str) -> str:
        """单次 chat 出站(字节闸内读响应+响应归一);错误归一为 EntityExtractError。"""
        try:
            raw = await self._post_chat(system, user_content)
            content = _parse_chat_content(raw)
        except _UNDELIVERED as e:
            raise EntityExtractError(_REASON_UNAVAILABLE, f"拒连({type(e).__name__})") from e
        except EntityExtractError:
            raise
        except httpx.TimeoutException as e:
            raise EntityExtractError(_REASON_TIMEOUT, f"读超时({type(e).__name__})") from e
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            reason = _REASON_RATELIMIT if code == 429 else _REASON_UNAVAILABLE
            raise EntityExtractError(reason, f"上游 HTTP {code}") from e
        except httpx.TransportError as e:
            raise EntityExtractError(_REASON_UNAVAILABLE, f"传输失败({type(e).__name__})") from e
        else:
            return content

    async def _record_usage(self, latency_ms: int, status: str) -> None:
        """出站留痕(sys_llm_usage;独立短事务,失败只告警不阻断)。

        status=结果态口径(与精排级一致):SUCCESS 仅在响应解析完整成功后,
        报文畸形/出站失败均 ERROR。trace_id 取请求级单源;tenant 置空:
        抽取器端口不带租户上下文,用量面按 task=doc_entity 聚合观测。"""
        try:
            async with get_sessionmaker()() as s:
                await s.execute(
                    text(
                        "INSERT INTO public.sys_llm_usage"
                        " (id, trace_id, tenant_id, model, task, prompt_tokens,"
                        "  completion_tokens, total_tokens, latency_ms, status)"
                        " VALUES (:id, :trace_id, NULL, :model, :task, 0, 0, 0,"
                        "         :latency_ms, :status)"
                    ),
                    {
                        "id": str(uuid7()),
                        "trace_id": get_trace_id(),
                        "model": self.model,
                        "task": "doc_entity",
                        "latency_ms": latency_ms,
                        "status": status,
                    },
                )
                await s.commit()
        except Exception:
            log.warning("实体抽取留痕失败(不阻断)", exc_info=True)

    async def extract(self, parent_text: str, child_texts: list[str]) -> list[list[str]]:
        """父块批量抽取:返回与 child_texts 一一对应的实体名列表(已规范化)。

        子块列表为空或父块无文本时零出站返回空(不留痕);抽多不惩罚
        (截断对齐)。留痕 status=结果态(报文畸形=ERROR)。"""
        if not child_texts or not parent_text.strip():
            return [[] for _ in child_texts]
        started = time.monotonic()
        status = "SUCCESS"
        try:
            numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(child_texts))
            user_content = f"# 父块\n{parent_text[:2000]}\n\n# 子块\n{numbered}"
            raw = await self._chat(_SYSTEM_PROMPT, user_content)
            return _parse_entities(raw, len(child_texts))
        except EntityExtractError:
            status = "ERROR"
            raise
        finally:
            await self._record_usage(int((time.monotonic() - started) * 1000), status)

    async def query_entities(self, question: str, *, max_entities: int = 8) -> list[str]:
        """问句实体识别(检索扩展路);失败抛 EntityExtractError 由调用方降级词法。

        留痕 status=结果态(与 extract 同口径);空问句零出站不留痕。"""
        question = question.strip()
        if not question:
            return []
        started = time.monotonic()
        status = "SUCCESS"
        try:
            raw = await self._chat(
                "你是问句实体抽取器:抽取问句中的专有名词、别名与关键业务概念,"
                "不抽泛词,实体名用原文写法。"
                '输出严格 JSON:{"entities": ["实体1", ...]},不输出任何其他文本。',
                f"问句:{question[:500]}",
            )
            data = _json_dict(raw, "响应报文畸形(对象缺失)")
            entities = _obj_list(data.get("entities"), "响应缺 entities 数组")
            out: list[str] = []
            seen: set[str] = set()
            for item in entities:
                name = normalize_entity_name(str(item))
                if name and name not in seen:
                    seen.add(name)
                    out.append(name)
            return out[:max_entities]
        except EntityExtractError:
            status = "ERROR"
            raise
        finally:
            await self._record_usage(int((time.monotonic() - started) * 1000), status)


def _is_private_host(hostname: str) -> bool:
    """私网/本机主机判定(实体 LLM 端点缺省拒绝,显式豁免才放行)。"""
    host = hostname.lower().strip(".")
    if host in ("localhost", "::1") or host.endswith(".local"):
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        o = [int(p) for p in parts]
        if o[0] in (10, 127) or (o[0] == 192 and o[1] == 168):
            return True
        if o[0] == 172 and 16 <= o[1] <= 31:
            return True
        if o[0] == 169 and o[1] == 254:
            return True
    return False


def build_doc_entity_extractor(settings: Settings | None = None) -> DocEntityExtractor | None:
    """按 KM_DOC_ENTITY_* 配置创建实体抽取器;未启用/配置残缺/出口非法返回 None。

    未启用=能力从未存在(零行为);出口校验:https 且非私网(私网需
    KM_DOC_ENTITY_ALLOW_PRIVATE_LLM=true 显式豁免,本地 dev 形态)。"""
    s = settings or get_settings()
    if not s.doc_entity_enabled:
        return None
    base_url = (s.doc_entity_llm_base_url or "").strip()
    key = (s.doc_entity_llm_api_key or "").strip()
    model = (s.doc_entity_llm_model or "").strip()
    if not base_url or not key or not model:
        return None
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    private = _is_private_host(hostname)
    if parsed.scheme != "https" or not hostname:
        # 非 https 仅在私网显式豁免形态放行(本地 dev 本机端点);公网 http 恒拒
        if not (private and s.doc_entity_allow_private_llm):
            log.warning("实体抽取端点须为 https 且含主机名,条目剔除,实体级不生效:%s", base_url)
            return None
    elif private and not s.doc_entity_allow_private_llm:
        log.warning("实体抽取端点为私网/本机地址且未显式豁免,条目剔除,实体级不生效:%s", hostname)
        return None
    return DocEntityExtractor(
        base_url=base_url,
        api_key=key,
        model=model,
        timeout_s=s.doc_entity_timeout_ms / 1000,
    )
