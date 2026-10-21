"""提示词装配预算测试:估算/锚/收敛方向纪律/不可切段/预算下发。"""

from typing import cast

import pytest

from kongming_agent import deps
from kongming_agent.graph.budget import (
    PromptBudgetExceeded,
    Segment,
    converge,
    estimate_tokens,
)
from kongming_agent.graph.nodes import (
    assemble_generate_user_prompt,
    legacy_generate_user_prompt,
)
from kongming_agent.graph.prompts import SQL_GEN_SYSTEM
from kongming_agent.settings import Settings


def _baseline_budget(segment_tokens: int, question: str = "q") -> int:
    """固定部分(纪律段+问句+标签)之上给被测段留 segment_tokens 的预算。"""
    fixed = (
        estimate_tokens(SQL_GEN_SYSTEM)
        + estimate_tokens(f"问题:{question}")
        + 24  # 装配标签和空段的预留开销
    )
    return fixed + segment_tokens


def _settings(**env: str) -> Settings:
    import os

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


# ── 1. 估算纯函数 ────────────────────────────────────────────────


def test_estimate_tokens_cjk_and_ascii() -> None:
    """CJK 1 tok/char、其余 1 tok/3.5 char 的保守分档。"""
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好") == 2
    # 10 个 ASCII → ceil(10/3.5)=3
    assert estimate_tokens("SELECT 1 ") == 3
    assert estimate_tokens("你好SELECT") == 2 + 2  # 2 CJK + ceil(6/3.5)=2


# ── 2. 锚 ─────────────────────────────────────────


def test_under_budget_byte_identical() -> None:
    """预算充足时，提示词装配结果与原格式一致。"""
    kwargs = dict(
        question="每个租户的订单数",
        memory_hint="用户关注华北区",
        schema_hint=(
            "public.orders(user_id, amount, created_at) -- 订单表\npublic.users(id, name) -- 用户表"
        ),
        relations_hint="orders.user_id → users.id",
        metric_hints="订单数 = COUNT(orders.id)",
        skill_terms="- 华北:region = 'NORTH'",
        skill_examples=(
            "fewshot SQL 仅文本\n1. 问:订单数\n   答 SQL 形态:SELECT COUNT(*) FROM orders"
        ),
        history_sql="SELECT COUNT(*) FROM orders WHERE created_at >= '2026-01-01'",
        extra='\n\n上次生成的 SQL 有误:column "x" does not exist\n请修正。',
    )
    legacy = legacy_generate_user_prompt(**kwargs)
    user, cut, _estimated = assemble_generate_user_prompt(**kwargs, budget_tokens=100_000)
    assert user == legacy
    assert cut == {}


def test_empty_segments_byte_identical() -> None:
    """全空段(零命中形态)同样逐字一致。"""
    kwargs = dict(
        question="在吗",
        memory_hint="",
        schema_hint="（未能检索到与问题相关的表结构）",
        relations_hint="",
        metric_hints="",
        skill_terms="",
        skill_examples="",
        history_sql="",
        extra="",
    )
    legacy = legacy_generate_user_prompt(**kwargs)
    user, cut, _estimated = assemble_generate_user_prompt(**kwargs, budget_tokens=100_000)
    assert user == legacy
    assert cut == {}


# ── 3. 收敛方向纪律 ───────────────────────────────────────


def _big_schema(tables: int, cols: int) -> str:
    lines: list[str] = []
    for t in range(tables):
        lines.append(f"public.t{t}({', '.join(f'c{c} int' for c in range(cols))}) -- 表{t}")
    return "\n".join(lines)


def test_history_sql_drops_from_oldest_end() -> None:
    """历史段只从最旧端丢(首位=最高相似保留),无中间掏空。"""
    history = "\n".join(f"SELECT {i} FROM t{i}" for i in range(20))  # noqa: S608  测试夹具文本
    user, cut, _ = assemble_generate_user_prompt(
        question="q",
        memory_hint="",
        schema_hint="public.t(a)",
        relations_hint="",
        metric_hints="",
        skill_terms="",
        skill_examples="",
        history_sql=history,
        extra="",
        budget_tokens=_baseline_budget(10),
    )
    seg = user.split("参考历史成功 SQL：\n")[1].split("\n\n生成 SQL：")[0]
    kept = seg.split("\n")
    assert kept[0] == "SELECT 0 FROM t0"  # 首位(最高相似)保留
    # 连续后缀,无中间掏空(夹具文本由本测试自产,不入任何查询)
    assert kept == [f"SELECT {i} FROM t{i}" for i in range(len(kept))]  # noqa: S608
    assert cut["history_sql"] == 20 - len(kept)


def test_evidence_drops_from_tail_and_first_exempt() -> None:
    """证据段(表结构)自尾部裁剪,首位恒保留。"""
    schema = _big_schema(8, 30)  # 8 张宽表
    user, cut, _ = assemble_generate_user_prompt(
        question="q",
        memory_hint="",
        schema_hint=schema,
        relations_hint="",
        metric_hints="",
        skill_terms="",
        skill_examples="",
        history_sql="",
        extra="",
        budget_tokens=_baseline_budget(120),
    )
    seg = user.split("可用表结构：\n")[1].split("\n\n生成 SQL：")[0]
    first_line = seg.split("\n")[0]
    assert first_line.startswith("public.t0(")  # 首位(最高相关)豁免
    assert cut.get("schema_hint", 0) > 0


def test_cut_priority_relations_before_schema() -> None:
    """跨段优先级:relations/metric/memory 先于 schema 被裁。"""
    schema = _big_schema(6, 30)
    user, cut, _ = assemble_generate_user_prompt(
        question="q",
        memory_hint="用户偏好",
        schema_hint=schema,
        relations_hint="a.x → b.y\n" * 40,
        metric_hints="m = SUM(x)\n" * 40,
        skill_terms="",
        skill_examples="",
        history_sql="",
        extra="",
        budget_tokens=_baseline_budget(150),
    )
    assert "relations" in cut
    assert "metric" in cut
    # relations/metric 被整段丢弃后 schema 仍保留(裁剪优先级殿后)
    assert "表关系参考" not in user
    assert "指标口径参考" not in user
    assert "可用表结构" in user


def test_skill_example_ban_prefix_kept() -> None:
    """skill_examples 尾裁时禁令前缀(fewshot SQL 仅文本)必须保留。"""
    examples = "fewshot SQL 仅文本,不执行" + "".join(
        f"\n{i}. 问:问题{i}\n   答 SQL 形态:SELECT {i}" for i in range(1, 12)
    )
    user, _cut, _ = assemble_generate_user_prompt(
        question="q",
        memory_hint="",
        schema_hint="public.t(a)",
        relations_hint="",
        metric_hints="",
        skill_terms="",
        skill_examples=examples,
        history_sql="",
        extra="",
        budget_tokens=_baseline_budget(80),
    )
    if "问法示例" in user:
        seg = user.split("问法示例：\n")[1].split("\n\n")[0]
        assert seg.startswith("fewshot SQL 仅文本,不执行")


# ── 4. 不可切段 ─────────────────────────────────────────


def test_uncuttable_segments_never_truncated() -> None:
    """纪律段/问句/selfheal 喂回永不被截;构造其超预算 → 如实失败分支。"""
    with pytest.raises(PromptBudgetExceeded):
        assemble_generate_user_prompt(
            question="超长问句" * 200,
            memory_hint="",
            schema_hint="",
            relations_hint="",
            metric_hints="",
            skill_terms="",
            skill_examples="",
            history_sql="",
            extra="",
            budget_tokens=50,
        )


def test_selfheal_extra_preserved_after_convergence() -> None:
    """收敛后 selfheal 喂回段与哨兵引导仍完整。"""
    extra = '\n\n上次生成的 SQL 有误：relation "t" does not exist\n请修正。核对清单'
    user, cut, _ = assemble_generate_user_prompt(
        question="q",
        memory_hint="",
        schema_hint=_big_schema(10, 30),
        relations_hint="",
        metric_hints="",
        skill_terms="",
        skill_examples="",
        history_sql="SELECT 1",
        extra=extra,
        budget_tokens=_baseline_budget(200),
    )
    assert extra in user
    assert "selfheal" not in cut


def test_converge_primitives_directly() -> None:
    """纯函数层:不可切段超预算即抛,不静默删。"""
    segs = [Segment("sys", "x" * 4000), Segment("q", "y" * 4000)]
    with pytest.raises(PromptBudgetExceeded):
        converge(segs, budget_tokens=100)
    # 可切段整段丢弃路径(lines=False)
    segs2 = [Segment("a", "z" * 4000, priority=1)]
    out = converge(segs2, budget_tokens=10)
    assert out.segments[0].text == ""
    assert out.cut == {"a": 1}


# ── 5. 注册表窗口与预算解析 ─────────────────────────────


def test_context_window_entry_and_default() -> None:
    """条目声明窗口优先;未声明回退全局缺省;畸形窗口不剔除条目。"""
    import json

    from kongming_agent.llm import LlmClient

    reg = json.dumps(
        [
            {
                "name": "wide",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk",
                "model": "m-wide",
                "context_window": 128000,
            },
            {
                "name": "broken",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk",
                "model": "m-broken",
                "context_window": -5,  # 畸形 → 未声明
            },
        ]
    )
    s = _settings(
        model_registry_json=reg,
        model_routes_json='{"SQL_GEN": "wide"}',
        llm_api_key="sk-main",
    )
    registry = s.parse_registry()
    assert "broken" in registry  # 畸形窗口不剔除条目
    assert registry["broken"].context_window is None
    client = LlmClient(s, usage_reporter=None)
    assert client.context_window("SQL_GEN") == 128000
    assert client.context_window("SUMMARIZE") == s.prompt_context_window_default
    # 预算 = 窗口 − 预留输出 − 安全边际
    assert client.prompt_budget("SQL_GEN") == (
        128000 - s.prompt_reserved_output_tokens - s.prompt_safety_margin_tokens
    )


# ── 节点级 trace 下发 ────────────────────────────


async def test_generate_emits_trace_on_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """预算触发时:被裁段落与裁切量随 trace 事件下发(可观测,不静默降质)。"""
    from kongming_agent.graph import nodes as nodes_mod

    events: list[tuple[str, dict[str, object]]] = []

    def fake_emit(event: str, data: dict[str, object]) -> None:
        events.append((event, data))

    monkeypatch.setattr(nodes_mod, "emit_event", fake_emit)

    class _NarrowLlm:
        def prompt_budget(self, task: str | None = None) -> int:
            return _baseline_budget(50)  # 固定部分之上仅留 50 tokens,强制收敛

        async def chat(self, system: str, user: str, **_kw: object) -> str:
            return "SELECT 1"

    monkeypatch.setattr(deps, "get_llm", lambda: _NarrowLlm())
    state: dict[str, object] = {
        "question": "q",
        "schema_hint": _big_schema(20, 3),  # 首位行短(豁免后仍有收敛空间)
        "tenant_key": "t",
        "trace_id": "tr",
    }
    await nodes_mod.generate(state)  # pyright: ignore[reportArgumentType]
    trace_events = [d for (e, d) in events if e == "trace"]
    assert len(trace_events) == 1
    fields = cast("dict[str, object]", trace_events[0]["fields"])
    assert fields["kind"] == "prompt_budget"
    assert fields["budget_tokens"] == _baseline_budget(50)
    cut = fields["cut"]
    assert isinstance(cut, dict) and "schema_hint" in cut


async def test_generate_no_trace_when_under_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """预算内不触发收敛:零 trace 事件(正常轮次零噪音)。"""
    from kongming_agent.graph import nodes as nodes_mod

    events: list[tuple[str, dict[str, object]]] = []

    def fake_emit2(event: str, data: dict[str, object]) -> None:
        events.append((event, data))

    monkeypatch.setattr(nodes_mod, "emit_event", fake_emit2)

    class _WideLlm:
        def prompt_budget(self, task: str | None = None) -> int:
            return 1_000_000

        async def chat(self, system: str, user: str, **_kw: object) -> str:
            return "SELECT 1"

    monkeypatch.setattr(deps, "get_llm", lambda: _WideLlm())
    state: dict[str, object] = {
        "question": "q",
        "schema_hint": "public.t(a)",
        "tenant_key": "t",
        "trace_id": "tr",
    }
    await nodes_mod.generate(state)  # pyright: ignore[reportArgumentType]
    assert events == []
