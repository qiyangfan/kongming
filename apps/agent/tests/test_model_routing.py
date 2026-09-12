"""021 multi-model-routing 测试:注册表解析/任务路由/usage 采集上报。"""

import json

from kongming_agent.llm import LlmClient
from kongming_agent.settings import Settings


def _settings(**env: str) -> Settings:
    import os

    # 环境隔离:白名单显式钉住(dashscope),不随本机 .env 的主模型 base_url
    # 漂移(muniu 网关切换后 allowed_hosts 含 127.0.0.1,dashscope 条目会被
    # 拒配致断言环境依赖性失败);调用方显式传入 model_allowed_hosts 时不覆盖。
    env.setdefault("model_allowed_hosts", "dashscope.aliyuncs.com")
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
    """路由命中注册表模型;usage 上报含 task/model/tokens/tenant_key(R1-L2 字段钉)。"""
    s = _settings(
        model_registry_json=REG,
        model_routes_json='{"SQL_GEN": "coder"}',
        llm_api_key="sk-main",
    )
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    # 替换客户端为替身(主模型与 coder 两个)
    main_comp, coder_comp = _FakeCompletions(), _FakeCompletions()
    client._client = _FakeClient(main_comp)  # type: ignore[assignment]
    client._pool["coder"] = (_FakeClient(coder_comp), "qwen-coder-plus")  # type: ignore[assignment]

    await client.chat("s", "u", task="SQL_GEN", trace_id="t1", tenant_key="sakila")
    await client.chat("s", "u", task="SUMMARIZE", trace_id="t2")  # 未配路由→主模型
    import asyncio

    await asyncio.sleep(0)  # fire-and-forget 上报跑一步
    await asyncio.sleep(0)
    assert coder_comp.models == ["qwen-coder-plus"]  # SQL_GEN 路由命中
    assert main_comp.models == ["qwen-plus"]  # SUMMARIZE 回退主模型
    # R1-L2:上报记录字段钉(漂移=api 422 静默丢量的防线)
    # 038:补 status/latency_ms(采集补全)
    assert len(recorder.records) == 2
    for rec in recorder.records:
        assert set(rec) == {
            "trace_id", "tenant_key", "model", "task",
            "prompt_tokens", "completion_tokens", "total_tokens",
            "status", "latency_ms",
        }
        assert rec["prompt_tokens"] == 11 and rec["total_tokens"] == 18
        assert rec["status"] == "SUCCESS"
        assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0
    assert recorder.records[0]["tenant_key"] == "sakila"
    assert recorder.records[0]["model"] == "qwen-coder-plus"
    assert recorder.records[1]["tenant_key"] is None  # 未传
    assert recorder.records[1]["model"] == "qwen-plus"


async def test_usage_report_failure_never_blocks() -> None:
    """上报异常(boom)主链路正常返回。"""
    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder(boom=True)
    client = LlmClient(s, usage_reporter=recorder)
    comp = _FakeCompletions()
    client._client = _FakeClient(comp)  # type: ignore[assignment]
    out = await client.chat("s", "u", task="SQL_GEN")
    assert out == "ok"


class _BoomCompletions:
    """供应商失败替身:create 恒抛超时异常(038 失败采集路径)。"""

    async def create(self, *, model: str, **kw: object):  # type: ignore[no-untyped-def]
        raise TimeoutError("Read timeout after 30s")


async def test_failure_call_reported_as_error() -> None:
    """038:失败调用也采集——status=ERROR、tokens=0、latency_ms 在场,
    异常仍按 LlmError 上抛(fail 语义不变)。"""
    import asyncio

    import pytest

    from kongming_agent.llm import LlmError

    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    client._client = _FakeClient(_BoomCompletions())  # type: ignore[assignment]
    with pytest.raises(LlmError):
        await client.chat("s", "u", task="SQL_GEN", trace_id="tr-fail")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(recorder.records) == 1
    rec = recorder.records[0]
    # R1-B4:失败路径字段集全键钉(与成功路径同构防线)
    assert set(rec) == {
        "trace_id", "tenant_key", "model", "task",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "status", "latency_ms",
    }
    assert rec["status"] == "ERROR"
    assert rec["prompt_tokens"] == 0 and rec["total_tokens"] == 0
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0
    assert rec["trace_id"] == "tr-fail"


class _EmptyChoicesCompletions:
    """R1-B2:供应商 200 但空 choices(应答畸形)——应落 ERROR 并收敛 LlmError。"""

    async def create(self, *, model: str, **kw: object):  # type: ignore[no-untyped-def]
        class _Resp:
            choices = []
            usage = None

        return _Resp()


async def test_malformed_response_reported_as_error() -> None:
    """R1-B2:空 choices 协议错不再裸 IndexError 500,归 ERROR 采集。"""
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
    """038:成功但供应商未回 usage(resp.usage=None)——同样落记录
    (旧语义 usage None 即丢弃,038 起调用次数不丢)。"""

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
