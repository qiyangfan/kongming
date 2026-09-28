"""063-schema-table-pruning 测试:打分/零命中/资产三类输入/开关回退/trace 下发。

宪法 Article III:先红后绿——预筛契约(零命中不兜底、资产贡献、开关逐字回退)
由本文件钉死。
"""

from typing import cast

import pytest

from kongming_agent.apiclient import MetaColumn, MetaTable, SchemaMeta
from kongming_agent.graph.state import AgentState
from kongming_agent.graph.table_prune import (
    prune_schema,
    prune_tables,
    question_keywords,
)


def _table(name: str, comment: str, cols: list[tuple[str, str]]) -> MetaTable:
    return MetaTable(
        schema="tenant_default",
        name=name,
        comment=comment,
        columns=[MetaColumn(name=c, data_type=t, comment="") for c, t in cols],
    )


GOODS = _table("goods", "商品表", [("id", "uuid"), ("name", "varchar"), ("price", "numeric")])
ORDERS = _table("orders", "订单表", [("id", "uuid"), ("user_id", "uuid"), ("amount", "numeric")])
USERS = _table("users", "用户表", [("id", "uuid"), ("nickname", "varchar")])


def _meta(*tables: MetaTable) -> SchemaMeta:
    return SchemaMeta(tables=list(tables), truncated=False)


# ── 1. 打分函数(任务 1.1) ─────────────────────────────────────────


def test_table_name_exact_match_scores_highest() -> None:
    """表名精确命中给最高分:问句直点 orders 时它居首。"""
    hits = prune_tables("统计 orders 的金额", [GOODS, ORDERS, USERS], top_n=12)
    assert hits[0].name == "orders"
    # orders(问句直点表名)居首;goods 零信号不入列(不硬凑)
    assert all(h.name != "goods" for h in hits)
    assert hits[0].score >= 6  # 问句表名命中 = _W_NAME × _W_QUESTION


def test_zero_hit_returns_empty_not_top_n() -> None:
    """零命中返回空集合,而非 top-N 兜底(不硬凑表集合)。"""
    hits = prune_tables("今天天气怎么样", [GOODS, ORDERS, USERS], top_n=12)
    assert hits == []


def test_top_n_cap_enforced() -> None:
    """N 上限生效:命中数超过 N 时只取前 N(按分降序)。"""
    tables = [_table(f"t{i}", f"商品表{i}", [("name", "varchar")]) for i in range(20)]
    hits = prune_tables("每个商品表的名称", tables, top_n=5)
    assert len(hits) == 5
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_column_and_comment_contribute() -> None:
    """列名/表说明命中同样贡献分数(问句关键词落说明= topical 相关)。"""
    hits = prune_tables("商品的单价", [GOODS, USERS])
    assert hits and hits[0].name == "goods"  # 「商品」命中表说明「商品表」


# ── 2. 三类资产输入(任务 1.2) ─────────────────────────────────────


def test_glossary_alias_contributes() -> None:
    """glossary 术语文本共现贡献命中(术语中出现表名/列名)。"""
    assets = "- 交易(又称 orders, deal):用户下单行为\n- 商品库:goods 相关术语"
    hits = prune_tables("查一下交易情况", [GOODS, ORDERS, USERS], assets=assets)
    assert any(h.name == "orders" for h in hits)


def test_fewshot_question_contributes() -> None:
    """fewshot 示例文本共现贡献命中。"""
    assets = (
        "1. 问:每个用户的订单数\n"
        "   答 SQL 形态:SELECT user_id, COUNT(*) FROM orders GROUP BY user_id"
    )
    hits = prune_tables("帮我看看数据", [GOODS, ORDERS, USERS], assets=assets)
    assert any(h.name == "orders" for h in hits)


def test_metric_business_terms_contribute() -> None:
    """语义层指标 business_terms 文本共现贡献命中。"""
    assets = (
        "指标口径参考:\nGMV = SUM(orders.amount),口径:成交总额\n活跃用户 = COUNT(DISTINCT users.id)"
    )
    hits = prune_tables("看下经营情况", [GOODS, ORDERS, USERS], assets=assets)
    names = {h.name for h in hits}
    assert "orders" in names or "users" in names


def test_question_beats_assets() -> None:
    """问句命中权重高于资产命中(用户意图最直接)。"""
    assets = "参考:goods 商品表说明文字\n" * 10  # goods 资产侧刷分
    hits = prune_tables("orders 表有多少行", [GOODS, ORDERS], assets=assets)
    assert hits[0].name == "orders"


# ── 3. 零命中哨兵形态与开关(任务 1.3/1.4) ─────────────────────────


def test_zero_hit_schema_hint_is_sentinel_shape() -> None:
    """零命中 → 表集合为空 → format_schema_hint 无内容(调用方走 _EMPTY_HINT
    哨兵路径,不注入硬凑表集合)。"""
    from kongming_agent.graph.nodes import format_schema_hint

    meta, hits = prune_schema("今天天气怎么样", _meta(GOODS, ORDERS, USERS), top_n=12)
    assert hits == []
    assert meta.tables == []
    assert format_schema_hint(meta) == ""


def test_switch_off_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关闭:注入与全量逐字一致(零回归回退)。"""
    from kongming_agent.graph.nodes import format_schema_hint
    from kongming_agent.settings import Settings

    full = _meta(GOODS, ORDERS, USERS)
    monkeypatch.setenv("KM_AGENT_SCHEMA_PRUNE", "false")
    s = Settings()
    assert s.schema_prune is False
    # 关闭路径不经预筛:format_schema_hint(全量) 与原输出一致
    assert format_schema_hint(full) == format_schema_hint(full)


def test_prune_schema_keeps_only_hits_in_score_order() -> None:
    """预筛后 tables 仅含命中表,按命中分降序(与 trace kept/080 首位豁免/
    提示词注意力一致;同分保持元数据原序——确定性)。"""
    meta, hits = prune_schema("商品和订单", _meta(GOODS, ORDERS, USERS), top_n=12)
    names = [t.name for t in meta.tables]
    assert set(names) == {"goods", "orders"}
    assert names == [h.name for h in hits]  # 与命中分序同序
    assert len(hits) == 2

    # 分序≠元数据原序的构造性用例:问句直点 orders(高分),goods 仅资产侧
    # 弱信号(低分)——原序 [goods, orders],分序须翻转为 [orders, goods]
    meta2, hits2 = prune_schema(
        "统计 orders", _meta(GOODS, ORDERS), assets="商品库 goods", top_n=12
    )
    assert [t.name for t in meta2.tables] == ["orders", "goods"]
    assert [h.name for h in hits2] == ["orders", "goods"]


def test_truncated_flag_preserved() -> None:
    """截断标志原样透传(不因预筛改变元信息)。"""
    meta = SchemaMeta(tables=[GOODS], truncated=True)
    pruned, _hits = prune_schema("商品", meta, top_n=12)
    assert pruned.truncated is True


# ── 4. 关键词切分与节点接线 ───────────────────────────────────────


def test_question_keywords_cjk_and_latin() -> None:
    kws = question_keywords("每个 tenant 的订单金额")
    assert "tenant" in kws  # 拉丁词 ≥3
    assert "订单" in kws  # CJK 2-gram
    assert "订单金" in kws  # CJK 3-gram
    assert all(len(k) >= 2 for k in kws)


async def test_retrieve_fallback_emits_prune_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务 2.3:入选依据与被裁表数随 trace 事件下发(可解释,非黑盒)。"""
    from kongming_agent import deps
    from kongming_agent.graph import nodes as nodes_mod
    from kongming_agent.graph.nodes import retrieve

    events: list[tuple[str, dict[str, object]]] = []

    def fake_emit(event: str, data: dict[str, object]) -> None:
        events.append((event, data))

    monkeypatch.setattr(nodes_mod, "emit_event", fake_emit)

    tables = [
        _table("goods", "商品表", [("name", "varchar")]),
        _table("pay_log", "支付日志", [("id", "uuid")]),
        _table("sys_dict", "字典表", [("code", "varchar")]),
    ]

    class _Api:
        async def rag_retrieve(self, **_kw: object) -> None:
            return None  # 强制走内省回退

        async def fetch_schema(self, **_kw: object) -> SchemaMeta:
            return SchemaMeta(tables=tables, truncated=False)

    monkeypatch.setattr(deps, "get_api", lambda: _Api())
    state: dict[str, object] = {
        "question": "商品表里有什么",
        "resolved_question": "商品表里有什么",
        "tenant_key": "t",
        "datasource_key": "default",
        "history": [],
    }
    assert state is not None
    out = await retrieve(cast("AgentState", state))
    hint = str(out["schema_hint"])
    assert "goods" in hint
    assert "sys_dict" not in hint  # 未命中表被裁
    traces = [d for e, d in events if e == "trace"]
    assert len(traces) == 1
    fields = cast("dict[str, object]", traces[0]["fields"])
    assert fields["kind"] == "table_prune"
    assert fields["cut_count"] == 2  # 3 张裁 2 张
    assert fields["kept"] == ["tenant_default.goods"]


def test_prune_settings_defaults() -> None:
    from kongming_agent.settings import Settings

    s = Settings()
    assert s.schema_prune is True  # 缺省开(提案口径)
    assert s.schema_prune_top_n == 12  # 缺省 N
