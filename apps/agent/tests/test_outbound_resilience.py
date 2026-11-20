"""-agent-outbound-resilience 测试:错误分类 / 重试语义 / 退避 / 降级链 / 用量每尝试一条。

假时钟策略:on_retry 记录退避断言 + sleep 替身收 0——不真等。

验证重试、退避和降级不会重复或遗漏用量记录。
"""

import asyncio
import json
import os
from typing import ClassVar

import httpx
import pytest

from kongming_agent.apiclient import ApiClient
from kongming_agent.llm import LlmClient, LlmError
from kongming_agent.retry import (
    call_with_retry,
    is_connect_phase_error,
    is_transient_error,
    retry_wait_ms,
)
from kongming_agent.settings import Settings


def _settings(**env: str) -> Settings:
    env.setdefault("model_allowed_hosts", "dashscope.aliyuncs.com")
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


# ── 1. 错误分类纯函数 ─────────────────────────────────────────────


class _StatusError(Exception):
    """鸭子类型状态码异常(模拟 openai APIStatusError / anthropic APIError)。"""

    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


class _RetryAfterError(Exception):
    """带 Retry-After 响应头的异常(openai APIStatusError.response.headers 同构)。"""

    def __init__(self, status: int, retry_after: str) -> None:
        super().__init__(f"http {status}")
        self.status_code = status
        self.response = httpx.Response(status, headers={"retry-after": retry_after})


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_status(status: int) -> None:
    assert is_transient_error(_StatusError(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_transient_http_status(status: int) -> None:
    assert is_transient_error(_StatusError(status)) is False


def test_transient_transport_errors() -> None:
    assert is_transient_error(httpx.ConnectError("refused")) is True
    assert is_transient_error(httpx.ReadTimeout("read timeout")) is True
    assert is_transient_error(httpx.ConnectTimeout("connect timeout")) is True
    assert is_transient_error(ConnectionResetError("peer reset")) is True
    assert is_transient_error(TimeoutError()) is True


def test_domain_errors_never_transient() -> None:
    """key 缺失/协议错等 LlmError(无 cause)一律非瞬时——重发无解。"""
    assert is_transient_error(LlmError("KM_AGENT_LLM_API_KEY 未配置")) is False
    assert is_transient_error(LlmError("未知出站协议: x")) is False
    # 应答畸形:LlmError from IndexError —— cause 非传输层 → 非瞬时
    wrapped = LlmError("LLM 应答畸形: empty choices")
    wrapped.__cause__ = IndexError("empty choices")
    assert is_transient_error(wrapped) is False


def test_llmerror_unwraps_cause_for_status() -> None:
    """协议层 LlmError(from APIStatusError(429))按 429 判瞬时。"""
    cause = _StatusError(429)
    wrapped = LlmError("anthropic 调用失败: boom")
    wrapped.__cause__ = cause
    assert is_transient_error(wrapped) is True


def test_connect_phase_classifier() -> None:
    """回调分层:仅连接层未送达可重试;读超时/5xx/4xx 一律不可重发(D2)。"""
    assert is_connect_phase_error(httpx.ConnectError("refused")) is True
    assert is_connect_phase_error(httpx.ConnectTimeout("connect timeout")) is True
    assert is_connect_phase_error(httpx.ReadTimeout("read timeout")) is False
    assert is_connect_phase_error(_StatusError(503)) is False
    assert is_connect_phase_error(_StatusError(429)) is False


# ── 2. 退避与 Retry-After 截断 ───────────────────────────────────


def test_retry_after_clipped_to_max_wait() -> None:
    """供应商 Retry-After: 60s 不得穿透上层 agent_timeout_sec——截断到上限。"""
    wait = retry_wait_ms(1, _RetryAfterError(429, "60"), base_delay_ms=500, max_wait_ms=5000)
    assert wait == 5000


def test_retry_after_smaller_respected() -> None:
    wait = retry_wait_ms(1, _RetryAfterError(429, "1"), base_delay_ms=500, max_wait_ms=5000)
    assert wait == 1000


def test_exponential_backoff_within_bound() -> None:
    """无 Retry-After:指数+全量 jitter,值域 [0, min(base*2^(n-1), max)]。"""
    for attempt, ceiling in [(1, 500), (2, 1000), (3, 2000), (4, 4000), (5, 5000)]:
        wait = retry_wait_ms(attempt, None, base_delay_ms=500, max_wait_ms=5000)
        assert 0 <= wait <= ceiling


def test_retry_after_malformed_ignored() -> None:
    """畸形 Retry-After 值退化为指数退避(不炸)。"""
    wait = retry_wait_ms(1, _RetryAfterError(429, "soon"), base_delay_ms=500, max_wait_ms=5000)
    assert 0 <= wait <= 500


# ── 3. 尝试循环(假时钟) ──────────────────────────────────────────


async def test_retry_loop_retries_transient_and_succeeds() -> None:
    """瞬时故障重发至成功;退避经 sleep 替身记录,不真等。"""
    waits: list[float] = []
    calls = 0

    async def attempt() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _StatusError(503)
        return "ok"

    out = await call_with_retry(
        attempt,
        max_attempts=3,
        base_delay_ms=500,
        max_wait_ms=5000,
        sleep=lambda s: _record(waits, s),
    )
    assert out == "ok"
    assert calls == 3
    assert len(waits) == 2  # 两次退避


async def _record(waits: list[float], seconds: float) -> None:
    waits.append(seconds)


async def test_retry_loop_non_transient_single_call() -> None:
    """非瞬时故障(400/配置错)只调一次,不消耗预算。"""
    calls = 0

    async def attempt() -> str:
        nonlocal calls
        calls += 1
        raise _StatusError(400)

    with pytest.raises(_StatusError):
        await call_with_retry(attempt, max_attempts=3, base_delay_ms=500, max_wait_ms=5000)
    assert calls == 1


async def test_retry_loop_hard_cap_fourth_call_never_happens() -> None:
    """任何失败序列下不得出现第 4 次调用(预算硬上限)。"""
    calls = 0

    async def attempt() -> str:
        nonlocal calls
        calls += 1
        raise _StatusError(503)

    with pytest.raises(_StatusError):
        await call_with_retry(
            attempt,
            max_attempts=3,
            base_delay_ms=500,
            max_wait_ms=5000,
            sleep=lambda s: asyncio.sleep(0),
        )
    assert calls == 3


async def test_retry_loop_retry_after_capped_by_max_wait() -> None:
    """Retry-After 60s 截断为 5s:sleep 替身记录的等待不超上限。"""
    waits: list[float] = []

    async def attempt() -> str:
        raise _RetryAfterError(429, "60")

    with pytest.raises(_RetryAfterError):
        await call_with_retry(
            attempt,
            max_attempts=2,
            base_delay_ms=500,
            max_wait_ms=5000,
            sleep=lambda s: _record(waits, s),
        )
    assert waits == [5.0]


# ── 4. LLM 出站点接入 ────────────────────────────────────────────


class _TransientCompletions:
    """前 N 次瞬时失败、之后成功的 completions 替身。"""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def create(self, *, model: str, **kw: object):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _StatusError(503)

        class _Usage:
            prompt_tokens = 5
            completion_tokens = 2
            total_tokens = 7

        class _Msg:
            content = "ok"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices: ClassVar[list[object]] = [_Choice()]
            usage = _Usage()

        return _Resp()


class _FakeClient:
    def __init__(self, comp: object) -> None:
        self.chat = type("Chat", (), {"completions": comp})()


class _Recorder:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def __call__(self, records: list[dict[str, object]]) -> None:
        self.records.extend(records)


async def _drain() -> None:
    for _ in range(4):
        await asyncio.sleep(0)


def _patch_main(client: LlmClient, comp: object) -> None:
    client._client = _FakeClient(comp)  # type: ignore[assignment]


async def test_chat_retries_transient_then_succeeds() -> None:
    """瞬时故障(503)触发重试,最终成功;用量每尝试一条(1 ERROR + 1 SUCCESS)。"""
    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    comp = _TransientCompletions(fail_times=1)
    _patch_main(client, comp)
    out = await client.chat("s", "u", task="SQL_GEN", trace_id="t")
    assert out == "ok"
    assert comp.calls == 2
    await _drain()
    assert len(recorder.records) == 2  # 1.6:每尝试一条,不合并
    by_status = [r["status"] for r in recorder.records]
    assert by_status == ["ERROR", "SUCCESS"]


async def test_chat_hard_cap_three_attempts() -> None:
    """恒定 503 下总尝试=3,usage 三条 ERROR,最终 LlmError 上抛。"""
    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    comp = _TransientCompletions(fail_times=99)
    _patch_main(client, comp)
    with pytest.raises(LlmError):
        await client.chat("s", "u", task="SQL_GEN")
    assert comp.calls == 3
    await _drain()
    assert len(recorder.records) == 3
    assert all(r["status"] == "ERROR" for r in recorder.records)


async def test_chat_non_transient_no_retry() -> None:
    """配置类/4xx 非瞬时故障只调一次。"""

    class _Bad:
        calls = 0

        async def create(self, *, model: str, **kw: object):  # type: ignore[no-untyped-def]
            _Bad.calls += 1
            raise _StatusError(400)

    s = _settings(llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    comp = _Bad()
    _patch_main(client, comp)
    with pytest.raises(LlmError):
        await client.chat("s", "u", task="SQL_GEN")
    assert _Bad.calls == 1  # 非瞬时只调一次


async def test_task_target_degraded_to_main_model() -> None:
    """任务路由目标瞬时故障耗尽 → 降级主模型;用量记录实际模型(主模型)。"""
    reg = json.dumps(
        [
            {
                "name": "coder",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-test",
                "model": "qwen-coder-plus",
            }
        ]
    )
    s = _settings(
        model_registry_json=reg,
        model_routes_json='{"SQL_GEN": "coder"}',
        llm_api_key="sk-main",
        llm_max_attempts="3",
    )
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    coder_comp = _TransientCompletions(fail_times=99)  # coder 恒坏
    main_comp = _TransientCompletions(fail_times=0)  # 主模型恒好
    client._pool["coder"] = (_FakeClient(coder_comp), "qwen-coder-plus", "openai")  # type: ignore[assignment]
    _patch_main(client, main_comp)
    out = await client.chat("s", "u", task="SQL_GEN", trace_id="t")
    assert out == "ok"
    assert coder_comp.calls == 2  # 主目标 2 次尝试(预算 3 = 2 主目标 + 1 降级)
    assert main_comp.calls == 1  # 降级目标 1 次(不再重试)
    await _drain()
    models = [r["model"] for r in recorder.records]
    assert models == ["qwen-coder-plus", "qwen-coder-plus", "qwen-plus"]
    statuses = [r["status"] for r in recorder.records]
    assert statuses == ["ERROR", "ERROR", "SUCCESS"]


async def test_bot_override_never_degrades() -> None:
    """Bot model_override 目标瞬时故障耗尽 → 不降级,直接 LlmError。"""
    reg = json.dumps(
        [
            {
                "name": "botmodel",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-test",
                "model": "qwen-bot",
            }
        ]
    )
    s = _settings(model_registry_json=reg, llm_api_key="sk-main")
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    bot_comp = _TransientCompletions(fail_times=99)
    main_comp = _TransientCompletions(fail_times=0)
    client._pool["botmodel"] = (_FakeClient(bot_comp), "qwen-bot", "openai")  # type: ignore[assignment]
    _patch_main(client, main_comp)
    with pytest.raises(LlmError):
        await client.chat_with_tools("s", [], [], task="REACT", model_override="botmodel")
    assert bot_comp.calls == 3  # 同目标重试满预算
    assert main_comp.calls == 0  # 不降级
    await _drain()
    assert all(r["model"] == "qwen-bot" for r in recorder.records)


async def test_fallback_entry_used_after_main_exhausted() -> None:
    """主模型也耗尽且配置了 fallback 条目 → 降级到该条目(1 次,不重试)。"""
    reg = json.dumps(
        [
            {
                "name": "coder",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-test",
                "model": "qwen-coder-plus",
            },
            {
                "name": "backup",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-test",
                "model": "qwen-backup",
            },
        ]
    )
    s = _settings(
        model_registry_json=reg,
        model_routes_json='{"SQL_GEN": "coder"}',
        llm_api_key="sk-main",
        llm_fallback_entry="backup",
    )
    recorder = _Recorder()
    client = LlmClient(s, usage_reporter=recorder)
    coder_comp = _TransientCompletions(fail_times=99)
    client._pool["coder"] = (_FakeClient(coder_comp), "qwen-coder-plus", "openai")  # type: ignore[assignment]
    bad_main = _TransientCompletions(fail_times=99)
    _patch_main(client, bad_main)
    backup_comp = _TransientCompletions(fail_times=0)
    client._pool["backup"] = (_FakeClient(backup_comp), "qwen-backup", "openai")  # type: ignore[assignment]
    out = await client.chat("s", "u", task="SQL_GEN")
    assert out == "ok"
    assert coder_comp.calls == 1  # 三级链各 1 次(预算 3)
    assert bad_main.calls == 1
    await _drain()
    models = [r["model"] for r in recorder.records]
    assert models[-1] == "qwen-backup"
    assert recorder.records[-1]["status"] == "SUCCESS"


async def test_startup_rejects_unknown_fallback_entry() -> None:
    """fallback 条目不在注册表——启动期告警并视为未配置(不拒启)。"""
    s = _settings(llm_api_key="sk-main", llm_fallback_entry="ghost")
    s.fail_closed_checks()  # 仅告警,不抛
    client = LlmClient(s, usage_reporter=_Recorder())
    main_comp = _TransientCompletions(fail_times=99)
    _patch_main(client, main_comp)
    with pytest.raises(LlmError):
        await client.chat("s", "u", task="SQL_GEN")
    # 未配置生效:仅主模型 3 次尝试,无第四次(ghost 不接)
    assert main_comp.calls == 3


# ── 控制面回调重试 ────────────────────────────


class _CountingPost:
    """按序号抛异常的 _post_once 替身(连接层/读超时可编排);耗尽剧本后按路径返回成功响应。"""

    def __init__(self, script: list[BaseException]) -> None:
        self.script = script
        self.calls = 0

    async def __call__(self, path: str, payload: dict[str, object]) -> httpx.Response:
        self.calls += 1
        idx = self.calls - 1
        if idx < len(self.script):
            raise self.script[idx]
        if "sql/execute" in path:
            return httpx.Response(
                200,
                json={"verdict": "PASS", "columns": ["c"], "rows": [], "duration_ms": 1},
            )
        return httpx.Response(200, json={"tables": [], "truncated": False})


def _api() -> ApiClient:
    return ApiClient(_settings(internal_token="tok"))  # noqa: S106  测试替身令牌,非真实凭证


async def test_fetch_schema_retries_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch_schema:连接层失败可重试(第 2 次成功)。"""
    api = _api()
    post = _CountingPost([httpx.ConnectError("refused")])
    monkeypatch.setattr(ApiClient, "_post_once", post)
    out = await api.fetch_schema(tenant_key="t")
    assert out is not None
    assert post.calls == 2


async def test_sql_execute_read_timeout_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """sql_execute:读超时(已送达)不重试——执行副作用不可假定幂等(D2)。"""
    api = _api()
    post = _CountingPost([httpx.ReadTimeout("read timeout")])
    monkeypatch.setattr(ApiClient, "_post_once", post)
    out = await api.sql_execute(trace_id="t", tenant_key="t", sql_text="SELECT 1")
    assert out.verdict == "ERROR"
    assert post.calls == 1


async def test_sql_execute_connect_failure_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """sql_execute:连接层未送达失败可重试(第 2 次成功,PASS)。"""
    api = _api()
    post = _CountingPost([httpx.ConnectError("refused")])
    monkeypatch.setattr(ApiClient, "_post_once", post)
    out = await api.sql_execute(trace_id="t", tenant_key="t", sql_text="SELECT 1")
    assert out.verdict == "PASS"
    assert post.calls == 2


async def test_rag_retrieve_retries_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _api()
    post = _CountingPost([httpx.ConnectError("refused")])
    monkeypatch.setattr(ApiClient, "_post_once", post)
    out = await api.rag_retrieve(tenant_key="t", datasource_key="default", question="q")
    assert out is not None
    assert post.calls == 2


async def test_report_usage_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """report_usage:任何失败都不重试(重复落库)。"""
    api = _api()

    class _Post:
        calls = 0

        async def __call__(self, path: str, payload: dict[str, object]) -> httpx.Response:
            type(self).calls += 1
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(ApiClient, "_post_once", _Post())
    await api.report_usage([{"model": "m"}])
    assert _Post.calls == 1


async def test_tool_execute_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_execute:任何失败都不重试(工具副作用不假定幂等)。"""
    api = _api()
    post = _CountingPost([httpx.ConnectError("refused")])
    monkeypatch.setattr(ApiClient, "_post_once", post)
    out = await api.tool_execute(
        trace_id="t",
        tenant_key="t",
        datasource_key="default",
        user_id=None,
        tool_name="x",
        args={},
    )
    assert out.status == "error"
    assert post.calls == 1
