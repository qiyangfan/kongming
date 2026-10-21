"""多模型路由测试:注册表解析/任务路由/usage 采集上报。"""

import json

import pytest

from kongming_agent.llm import LlmClient
from kongming_agent.settings import Settings


def _settings(**env: str) -> Settings:
    import os

    # 显式配置模型主机白名单,避免本机 .env 改变路由测试结果;
    # 调用方传入 model_allowed_hosts 时保留其设置。
    env.setdefault("model_allowed_hosts", "dashscope.aliyuncs.com")
    # 显式设置协议,避免读取本机 .env。
    env.setdefault("llm_protocol", "openai")
    old = {k: os.environ.get(f"KM_AGENT_{k.upper()}") for k in env}
    for k, v in env.items():
        os.environ[f"KM_AGENT_{k.upper()}"] = v
    try:
        return Settings()
    finally:
        for k, oldv in old.items():
            key = f"KM_AGENT_{k.upper()}"
            if oldv is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = oldv


REG = json.dumps(
    [
        {
            "name": "coder",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-test",
            "model": "qwen-coder-plus",
            "capabilities": ["code"],
        },
        {
            "name": "fast",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-test2",
            "model": "qwen-turbo",
        },
    ]
)


def test_empty_registry_falls_back() -> None:
    s = _settings(model_registry_json="")
    assert s.parse_registry() == {}
    assert s.parse_routes() == {}


def test_registry_parse_and_whitelist() -> None:
    reg = json.loads(REG)
    reg.append({"name": "evil", "base_url": "https://evil.example.com/v1", "api_key": "k"})
    s = _settings(model_registry_json=json.dumps(reg))
    out = s.parse_registry()
    assert set(out) == {"coder", "fast"}  # evil 主机不在白名单(缺省=dashscope)被拒
    assert out["coder"].model == "qwen-coder-plus"


def test_registry_malformed_and_duplicates() -> None:
    s = _settings(model_registry_json="{bad json")
    assert s.parse_registry() == {}
    s2 = _settings(model_registry_json='[{"name":"a"}]')  # 缺字段
    assert s2.parse_registry() == {}
    dup = json.dumps(
        [
            {"name": "x", "base_url": "https://dashscope.aliyuncs.com/v1", "api_key": "k"},
            {"name": "x", "base_url": "https://dashscope.aliyuncs.com/v1", "api_key": "k2"},
        ]
    )
    s3 = _settings(model_registry_json=dup)
    assert len(s3.parse_registry()) == 1  # 重名剔除其一


def test_routes_parse() -> None:
    s = _settings(model_routes_json='{"SQL_GEN": "coder"}')
    assert s.parse_routes() == {"SQL_GEN": "coder"}
    s2 = _settings(model_routes_json="bad")
    assert s2.parse_routes() == {}


class _FakeCompletions:
    """记录 create 的 model;返回带 usage 的最小应答。"""

    def __init__(self) -> None:
        self.models: list[str] = []

    async def create(self, *, model: str, **kw: object):  # type: ignore[no-untyped-def]
        self.models.append(model)

        class _Usage:
            prompt_tokens = 11
            completion_tokens = 7
            total_tokens = 18

        class _Msg:
            content = "ok"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        return _Resp()


class _FakeClient:
    def __init__(self, comp: _FakeCompletions) -> None:
        self.chat = type("Chat", (), {"completions": comp})()


class _Recorder:
    def __init__(self, *, boom: bool = False) -> None:
        self.records: list[dict[str, object]] = []
        self.boom = boom

    async def __call__(self, records: list[dict[str, object]]) -> None:
        if self.boom:
            raise RuntimeError("api 不可达")
        self.records.extend(records)


async def test_task_routing_and_usage_report() -> None:
    """路由选择注册表模型,用量上报包含 task、model、token 和租户字段。"""
    s = _settings(
        model_registry_json=REG,
        model_routes_json='{"SQL_GEN": "coder"}',
        llm_api_key="sk-main",
    )
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    # 替换客户端为替身(主模型与 coder 两个;池条目多协议形态 (client, model, protocol))
    main_comp, coder_comp = _FakeCompletions(), _FakeCompletions()
    client._client = _FakeClient(main_comp)  # type: ignore[assignment]
    client._pool["coder"] = (_FakeClient(coder_comp), "qwen-coder-plus", "openai")  # type: ignore[assignment]

    await client.chat("s", "u", task="SQL_GEN", trace_id="t1", tenant_key="sakila")
    await client.chat("s", "u", task="SUMMARIZE", trace_id="t2")  # 未配路由→主模型
    import asyncio

    await asyncio.sleep(0)  # fire-and-forget 上报跑一步
    await asyncio.sleep(0)
    assert coder_comp.models == ["qwen-coder-plus"]  # SQL_GEN 路由命中
    assert main_comp.models == ["qwen-plus"]  # SUMMARIZE 回退主模型
    # API 校验上报字段;记录同时包含请求状态和延迟。
    assert len(recorder.records) == 2
    for rec in recorder.records:
        assert set(rec) == {
            "trace_id",
            "tenant_key",
            "model",
            "task",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "status",
            "latency_ms",
            "protocol",  # 出站协议归因
        }
        assert rec["prompt_tokens"] == 11 and rec["total_tokens"] == 18
        assert rec["status"] == "SUCCESS"
        assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0
    assert recorder.records[0]["tenant_key"] == "sakila"
    assert recorder.records[0]["model"] == "qwen-coder-plus"
    assert recorder.records[1]["tenant_key"] is None  # 未传
    assert recorder.records[1]["model"] == "qwen-plus"


async def test_usage_report_failure_never_blocks() -> None:
    """用量上报异常时模型调用仍正常返回。"""
    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder(boom=True)
    client = LlmClient(s, usage_reporter=recorder)
    comp = _FakeCompletions()
    client._client = _FakeClient(comp)  # type: ignore[assignment]
    out = await client.chat("s", "u", task="SQL_GEN")
    assert out == "ok"


class _BoomCompletions:
    """供应商失败替身:create 恒抛超时异常(失败采集路径)。"""

    async def create(self, *, model: str, **kw: object):  # type: ignore[no-untyped-def]
        raise TimeoutError("Read timeout after 30s")


async def test_failure_call_reported_as_error() -> None:
    """失败调用也采集——status=ERROR、tokens=0、latency_ms 在场,
    异常仍按 LlmError 上抛(fail 语义不变)。"""
    import asyncio

    import pytest

    from kongming_agent.llm import LlmError

    # 读超时属瞬时故障——重试满预算(3 次),每尝试一条 usage(不再合并)
    s = _settings(
        llm_api_key="sk-main",
        llm_retry_base_ms="1",
        llm_retry_max_wait_ms="1",
    )
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    client._client = _FakeClient(_BoomCompletions())  # type: ignore[assignment]
    with pytest.raises(LlmError):
        await client.chat("s", "u", task="SQL_GEN", trace_id="tr-fail")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(recorder.records) == 3
    assert all(r["status"] == "ERROR" for r in recorder.records)
    rec = recorder.records[0]
    # 失败路径字段集全键钉(与成功路径同构防线)
    assert set(rec) == {
        "trace_id",
        "tenant_key",
        "model",
        "task",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "status",
        "latency_ms",
        "protocol",  # 出站协议归因
    }
    assert rec["status"] == "ERROR"
    assert rec["prompt_tokens"] == 0 and rec["total_tokens"] == 0
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0
    assert rec["trace_id"] == "tr-fail"


class _EmptyChoicesCompletions:
    """供应商 200 但空 choices(应答畸形)——应落 ERROR 并收敛 LlmError。"""

    async def create(self, *, model: str, **kw: object):  # type: ignore[no-untyped-def]
        class _Resp:
            choices = []
            usage = None

        return _Resp()


async def test_malformed_response_reported_as_error() -> None:
    """空 choices 协议错不再裸 IndexError 500,归 ERROR 采集。"""
    import asyncio

    import pytest

    from kongming_agent.llm import LlmError

    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    client._client = _FakeClient(_EmptyChoicesCompletions())  # type: ignore[assignment]
    with pytest.raises(LlmError):
        await client.chat("s", "u", task="SQL_GEN", trace_id="tr-malformed")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(recorder.records) == 1
    assert recorder.records[0]["status"] == "ERROR"
    assert recorder.records[0]["trace_id"] == "tr-malformed"


async def test_success_without_usage_still_reported() -> None:
    """成功但供应商未回 usage(resp.usage=None)——同样落记录,调用次数不丢。"""

    class _NoUsageCompletions:
        async def create(self, *, model: str, **kw: object):  # type: ignore[no-untyped-def]
            class _Msg:
                content = "ok"

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]
                usage = None

            return _Resp()

    import asyncio

    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    client._client = _FakeClient(_NoUsageCompletions())  # type: ignore[assignment]
    out = await client.chat("s", "u")
    assert out == "ok"
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(recorder.records) == 1
    assert recorder.records[0]["status"] == "SUCCESS"
    assert recorder.records[0]["total_tokens"] == 0


# ── 多协议出站:条目/主模型协议声明与 fail-closed 校验 ──


def test_protocol_defaults_openai() -> None:
    """缺省:条目未声明 protocol 与显式 openai 一致;主模型 env 缺省 openai。"""
    s = _settings(model_registry_json=REG)
    out = s.parse_registry()
    assert all(e.protocol == "openai" for e in out.values())
    assert s.llm_protocol == "openai"
    s2 = _settings(model_registry_json=REG, llm_protocol="openai")
    assert all(e.protocol == "openai" for e in s2.parse_registry().values())
    assert s2.llm_protocol == "openai"


def test_registry_invalid_protocol_entry_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """条目 protocol 取值域外 = 与缺字段同级 fail-closed:剔除告警,其余条目可用。"""
    import logging

    logging.getLogger("kongming_agent.settings").disabled = False
    reg = json.loads(REG)
    reg.append(
        {
            "name": "weird",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-x",
            "model": "m1",
            "protocol": "grpc",
        }
    )
    s = _settings(model_registry_json=json.dumps(reg))
    with caplog.at_level("WARNING", logger="kongming_agent.settings"):
        out = s.parse_registry()
    assert set(out) == {"coder", "fast"}  # 仅畸形条目剔除,不裸崩
    assert any("protocol" in r.getMessage() for r in caplog.records)


def test_main_model_invalid_protocol_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    """主模型 KM_AGENT_LLM_PROTOCOL 非法:启动告警并回退 openai,不拒启。"""
    import logging

    logging.getLogger("kongming_agent.settings").disabled = False
    s = _settings(llm_protocol="grpc")
    with caplog.at_level("WARNING", logger="kongming_agent.settings"):
        s.fail_closed_checks()
    assert s.llm_protocol == "openai"
    assert any("KM_AGENT_LLM_PROTOCOL" in r.getMessage() for r in caplog.records)


# ── 遗留计价键按未知键忽略(不拒配)──


def test_legacy_price_keys_ignored_not_rejected() -> None:
    """条目携带 price_input/price_output 时正常生效,与未知键同形态。"""
    reg = json.dumps(
        [
            {
                "name": "legacy",
                "base_url": "https://dashscope.aliyuncs.com/v1",
                "api_key": "k",
                "price_input": 0.8,
                "price_output": 2.0,
            }
        ]
    )
    s = _settings(model_registry_json=reg)
    out = s.parse_registry()
    assert "legacy" in out
    assert out["legacy"].model == "legacy"
    # ModelEntry 无计价字段
    assert not hasattr(out["legacy"], "price_input")
    assert not hasattr(out["legacy"], "price_output")


def test_model_entry_has_no_price_fields() -> None:
    from dataclasses import fields

    from kongming_agent.settings import ModelEntry

    names = {f.name for f in fields(ModelEntry)}
    assert "price_input" not in names
    assert "price_output" not in names
