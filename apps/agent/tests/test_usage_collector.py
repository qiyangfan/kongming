"""035 agent 侧 request 级 usage 收集器测试:收集器在场=只收集不上报
(bot 链路唯一上报口=响应携带);缺省=fire-and-forget 零回归;reset 后
恢复旧路。"""

# pyright: reportPrivateUsage=false
# 测试惯例:复用 test_model_routing 的私有替身与 _settings(仓库测试约定,不改名)

from kongming_agent.llm import (
    LlmClient,
    reset_usage_collector,
    set_usage_collector,
)
from tests.test_model_routing import (
    _FakeClient,
    _FakeCompletions,
    _Recorder,
    _settings,
)


async def test_collector_present_collects_only() -> None:
    """收集器在场:records 带 seq 入列,reporter 零调用。"""
    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    comp = _FakeCompletions()
    client._client = _FakeClient(comp)  # type: ignore[assignment]
    collector: list[dict[str, object]] = []
    token = set_usage_collector(collector)
    try:
        await client.chat("s", "u", task="REACT", trace_id="t1", tenant_key="sakila")
        await client.chat("s", "u", task="SUMMARIZE", trace_id="t1")
    finally:
        reset_usage_collector(token)
    import asyncio

    await asyncio.sleep(0)
    assert recorder.records == []  # 未走 fire-and-forget
    assert len(collector) == 2
    assert collector[0]["seq"] == 0 and collector[1]["seq"] == 1
    assert collector[0]["trace_id"] == "t1" and collector[0]["tenant_key"] == "sakila"
    # 038:补 status/latency_ms(采集补全;bot sink 侧同步透传)
    assert set(collector[0]) == {
        "trace_id",
        "tenant_key",
        "model",
        "task",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "seq",
        "status",
        "latency_ms",
        "protocol",  # 077:出站协议归因
    }


async def test_collector_absent_keeps_fire_and_forget() -> None:
    """缺省(chat 渠道):reporter 照旧上报(021 零回归);reset 后恢复。"""
    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    comp = _FakeCompletions()
    client._client = _FakeClient(comp)  # type: ignore[assignment]
    collector: list[dict[str, object]] = []
    token = set_usage_collector(collector)
    reset_usage_collector(token)  # 立即复位
    await client.chat("s", "u", task="SQL_GEN", trace_id="t9")
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert collector == []  # 收集器已复位不入列
    assert len(recorder.records) == 1  # 旧路上报恢复
    assert "seq" not in recorder.records[0]  # 旧路 record 无 seq 字段
