"""运行期依赖工厂(LLM/控制面客户端)。

get_llm 按(设置指纹, 事件循环)缓存 LlmClient(021① 清偿):单次图执行
内多次 get_llm 复用同实例,不再每请求重建 N+2 个 AsyncOpenAI 客户端池;
指纹变更时同 loop 旧实例 fire-and-forget aclose 后重建(连接关闭不依赖
GC 时机),loop 已关闭的条目直接清除(其连接无法再异步关闭,弃给进程
回收——仅测试面会频繁换 loop;loop 以 weakref 持有不阻其回收)。
测试经 monkeypatch 替换本模块的 get_llm/get_api 注入替身(缓存不触发);
直接触达缓存的测试用 reset_llm_cache() 复位。
"""

import asyncio
import weakref

from kongming_agent.apiclient import ApiClient
from kongming_agent.llm import LlmClient
from kongming_agent.settings import Settings

_CLOSE_TASKS: set[asyncio.Task[None]] = set()  # R1-B4:aclose task 强引用(完成即摘)

# key=(设置指纹, id(loop));value=(client, loop weakref)
_llm_cache: dict[
    tuple[tuple[str, ...], int],
    tuple[LlmClient, "weakref.ref[asyncio.AbstractEventLoop]"],
] = {}


def _fingerprint(s: Settings) -> tuple[str, ...]:
    """影响 LlmClient 构造/行为的全部设置(变更即重建池;R1-B3 补齐:
    embedding 分批参数与 ApiClient 上报通道配置一并入指纹)。"""
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
    # R1-F3:命中必须校验 loop weakref——id(loop) 可能被复用(死 loop 回收后
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
        t = loop.create_task(_close_stale())  # R1-B4:强引用防 GC
        _CLOSE_TASKS.add(t)
        t.add_done_callback(_CLOSE_TASKS.discard)  # R2-B1:完成即摘(同 _BACKGROUND_REPORTS)
    return client


def get_api() -> ApiClient:
    return ApiClient(Settings())
