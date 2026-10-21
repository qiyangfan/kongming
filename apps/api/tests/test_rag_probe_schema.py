# pyright: reportPrivateUsage=false
"""验证召回试跑响应模型的字段保留，覆盖正常结果、零知识旁路和关系扩展关闭三种形态。"""

from typing import cast

import pytest

from kongming.rag.schemas import RecallProbeOut

# ── service.recall_probe 实际返回形态样例 ──────────────────────────────

SAMPLE_FULL: dict[str, object] = {
    "question": "查询库存数量",
    "gate_skipped": None,
    "expansion": {
        "enabled": True,
        "tables": [
            {
                "schema_name": "public",
                "table_name": "customers",
                "via": {
                    "source_table": "public.orders",
                    "target_table": "public.customers",
                    "join_keys": [{"left_col": "customer_id", "right_col": "id"}],
                    "source": "manual",
                },
            }
        ],
        "fused_after": [
            {"schema_name": "public", "table_name": "orders"},
            {"schema_name": "public", "table_name": "customers"},
        ],
    },
    "vector": {
        "hits": [
            {"schema_name": "public", "table_name": "orders", "score": 0.87, "source": "vector"}
        ],
        "verdict": {
            "passed": True,
            "reason": "passed",
            "top1": 0.87,
            "mean_top_n": 0.87,
            "count": 1,
        },
        "dropped": 0,
    },
    "fulltext": {
        "hits": [],
        "verdict": {
            "passed": False,
            "reason": "no_hits",
            "top1": 0.0,
            "mean_top_n": 0.0,
            "count": 0,
        },
        "dropped": 0,
    },
    "fused": [{"schema_name": "public", "table_name": "orders", "score": 0.016393}],
}

SAMPLE_NO_KNOWLEDGE: dict[str, object] = {
    "question": "商品表有哪些列",
    "gate_skipped": "no_knowledge",
    "expansion": {"enabled": True, "tables": [], "fused_after": []},
    "vector": {"hits": [], "verdict": None, "dropped": 0},
    "fulltext": {"hits": [], "verdict": None, "dropped": 0},
    "fused": [],
}

SAMPLE_EXPANSION_OFF: dict[str, object] = {
    "question": "查询库存数量",
    "gate_skipped": None,
    "expansion": {
        "enabled": False,
        "tables": [],
        "fused_after": [{"schema_name": "public", "table_name": "orders"}],
    },
    "vector": {
        "hits": [
            {"schema_name": "public", "table_name": "orders", "score": 0.9, "source": "vector"}
        ],
        "verdict": {
            "passed": True,
            "reason": "passed",
            "top1": 0.9,
            "mean_top_n": 0.9,
            "count": 1,
        },
        "dropped": 0,
    },
    "fulltext": {
        "hits": [
            {"schema_name": "public", "table_name": "orders", "score": 0.5, "source": "fulltext"}
        ],
        "verdict": {
            "passed": True,
            "reason": "passed",
            "top1": 0.5,
            "mean_top_n": 0.5,
            "count": 1,
        },
        "dropped": 0,
    },
    "fused": [
        {"schema_name": "public", "table_name": "orders", "score": 0.032787},
    ],
}


def _shape(value: object) -> object:
    """递归提取「键形态」:dict→键名到子形态;list→首元素形态;标量→类型名。

    None 值保留键位(键存在值为 None ≠ 键缺失),键集丢失在此暴露。"""
    if isinstance(value, dict):
        # isinstance 收窄出的键型对 pyright 是 Unknown,显式 cast 定型
        entries = cast("dict[object, object]", value)
        return {k: _shape(v) for k, v in entries.items()}
    if isinstance(value, list):
        items = cast("list[object]", value)
        return [_shape(items[0])] if items else []
    return type(value).__name__


@pytest.mark.parametrize(
    ("sample", "label"),
    [
        (SAMPLE_FULL, "完整形态(verdict 齐备+扩展非空)"),
        (SAMPLE_NO_KNOWLEDGE, "零知识旁路形态"),
        (SAMPLE_EXPANSION_OFF, "扩展未开启形态"),
    ],
)
def test_probe_model_roundtrip(sample: dict[str, object], label: str) -> None:
    """service 实际返回形态 → 模型解析 → 序列化键集无丢失。"""
    model = RecallProbeOut.model_validate(sample)
    assert _shape(model.model_dump(mode="python")) == _shape(sample), label


def test_probe_full_values_preserved() -> None:
    """关键值往返不变形:原生分/判定归因/经由关系/被裁数逐值保留。"""
    model = RecallProbeOut.model_validate(SAMPLE_FULL)
    assert model.question == "查询库存数量"
    assert model.gate_skipped is None
    vec_hit = model.vector.hits[0]
    assert (vec_hit.schema_name, vec_hit.table_name, vec_hit.score, vec_hit.source) == (
        "public",
        "orders",
        0.87,
        "vector",
    )
    assert model.vector.verdict is not None
    assert (model.vector.verdict.passed, model.vector.verdict.reason) == (True, "passed")
    assert model.vector.dropped == 0
    expansion = model.expansion.tables[0]
    assert (expansion.schema_name, expansion.table_name) == ("public", "customers")
    assert expansion.via.source_table == "public.orders"
    assert expansion.via.join_keys == [{"left_col": "customer_id", "right_col": "id"}]
    assert [t.table_name for t in model.expansion.fused_after] == ["orders", "customers"]
    assert model.fused[0].score == pytest.approx(0.016393)


def test_probe_no_knowledge_bypass_shape() -> None:
    """零知识旁路如实透出:gate_skipped=no_knowledge,两路 verdict=None(非裁剪结论)。"""
    model = RecallProbeOut.model_validate(SAMPLE_NO_KNOWLEDGE)
    assert model.gate_skipped == "no_knowledge"
    assert model.vector.verdict is None
    assert model.fulltext.verdict is None
    assert model.vector.dropped == 0
    assert model.fulltext.dropped == 0
    assert model.expansion.tables == []
