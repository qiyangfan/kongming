"""运行期 LLM 与控制面客户端工厂。

LLM 客户端按配置指纹与事件循环缓存;配置变更时关闭旧实例并重建。"""

import asyncio
import weakref

from kongming_agent.apiclient import ApiClient
from kongming_agent.llm import LlmClient
from kongming_agent.settings import Settings

_CLOSE_TASKS: set[asyncio.Task[None]] = set()  # aclose task 强引用(完成即摘)

# key=(设置指纹, id(loop));value=(client, loop weakref)
_llm_cache: dict[
    tuple[tuple[str, ...], int],
    tuple[LlmClient, "weakref.ref[asyncio.AbstractEventLoop]"],
] = {}


def _fingerprint(s: Settings) -> tuple[str, ...]:
    """影响 LlmClient 构造/行为的全部设置(变更即重建池),
    含 embedding 分批参数与 ApiClient 上报通道配置。"""
    return (
        s.llm_api_key or "",
        s.llm_base_url,
        s.llm_model,
        str(s.llm_timeout_sec),
        s.model_registry_json,
        s.model_routes_json,
        s.model_allowed_hosts,
        s.embedding_api_key or "",
        s.embedding_base_url or "",
        s.embedding_model,
        str(s.embedding_timeout_sec),
        str(s.embedding_max_texts),
        str(s.embedding_batch_size),
        str(s.llm_max_image_mb),
        # 出站韧性:重试预算/退避/降级条目影响 LlmClient 行为,一并入指纹
        str(s.llm_max_attempts),
        str(s.llm_retry_base_ms),
        str(s.llm_retry_max_wait_ms),
        s.llm_fallback_entry,
        # 装配预算参数影响 generate 收敛行为
        str(s.prompt_context_window_default),
        str(s.prompt_reserved_output_tokens),
        str(s.prompt_safety_margin_tokens),
        s.api_base_url,
        str(s.api_timeout_sec),
        s.internal_token or "",
    )


def reset_llm_cache() -> None:
    """清空缓存(测试复位用;不关连接——旧实例由调用方按需 aclose)。"""
    _llm_cache.clear()


def get_llm() -> LlmClient:
    settings = Settings()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 同步上下文(理论不可达,图节点/端点恒 async):退化为不缓存
        return LlmClient(settings, usage_reporter=ApiClient(settings).report_usage)
    loop_id = id(loop)
    key = (_fingerprint(settings), loop_id)
    cached = _llm_cache.get(key)
    # 命中必须校验 loop weakref——id(loop) 可能被复用(死 loop 回收后
    # 新 loop 撞同地址),不校验会把绑定死 loop 的客户端跨 loop 复用
    if cached is not None and cached[1]() is loop:
        return cached[0]
    # 清理:同 loop 旧指纹条目(fire-and-forget aclose);其他 loop 的
    # 条目若该 loop 已死(weakref 空/is_closed)直接弃,否则保留
    stale_same_loop: list[LlmClient] = []
    for k in [k for k in _llm_cache if k != key]:
        if k[1] == loop_id:
            stale_same_loop.append(_llm_cache.pop(k)[0])
            continue
        other_loop = _llm_cache[k][1]()
        if other_loop is None or other_loop.is_closed():
            del _llm_cache[k]
    client = LlmClient(settings, usage_reporter=ApiClient(settings).report_usage)
    _llm_cache[key] = (client, weakref.ref(loop))

    async def _close_stale() -> None:
        for c in stale_same_loop:
            await c.aclose()

    if stale_same_loop:
        t = loop.create_task(_close_stale())  # 强引用防 GC
        _CLOSE_TASKS.add(t)
        t.add_done_callback(_CLOSE_TASKS.discard)  # 完成即摘(同 _BACKGROUND_REPORTS)
    return client


def get_api() -> ApiClient:
    return ApiClient(Settings())
