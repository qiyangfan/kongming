"""104 bot-store 版本化:快照纯函数测试(测试先行,任务 2.1)。

覆盖:定义→快照白名单定型/规范化、一致性判定(规范化后逐项相等)、
快照键集断言(新增 Bot 定义字段时快照白名单同步的防线)。
"""

from typing import cast

from kongming.store.snapshot import (
    SNAPSHOT_FIELDS,
    build_snapshot,
    snapshots_equal,
)

_BASE = dict(
    name="销售助手",
    greeting="你好,我是销售助手",
    suggestions=["本月 GMV", "退货率"],
    system_prompt="你是销售分析助手",
    tools=["execute_sql", "rag_retrieve"],
)


def test_snapshot_key_set_is_pinned() -> None:
    """快照键集断言(防线):白名单必须与既定五字段逐字一致——
    Bot 定义新增字段时,此断言强迫开发者显式裁决「进不进快照」,
    防止白名单静默漂移(jsonb 读侧定型依赖该键集,ADR-005)。"""
    assert SNAPSHOT_FIELDS == ("name", "greeting", "suggestions", "system_prompt", "tools")


def test_build_snapshot_whitelist_and_types() -> None:
    """定型:只收白名单五字段,键集精确、值类型规范(str / list[str])。"""
    snap = build_snapshot(**_BASE)  # type: ignore[arg-type]
    assert set(snap.keys()) == set(SNAPSHOT_FIELDS)
    assert snap["name"] == "销售助手"
    assert snap["greeting"] == "你好,我是销售助手"
    assert snap["suggestions"] == ["本月 GMV", "退货率"]
    assert snap["system_prompt"] == "你是销售分析助手"
    assert snap["tools"] == ["execute_sql", "rag_retrieve"]


def test_build_snapshot_normalization() -> None:
    """规范化:建议问题逐条截断 200 字符且至多 5 条;非 str 入参收编为 str。"""
    long = "x" * 300
    snap = build_snapshot(
        name="n",
        greeting="g",
        suggestions=[long, "b", "c", "d", "e", "f"],  # 6 条 → 前 5 条
        system_prompt="p",
        tools=["t1"],
    )
    suggestions = cast("list[str]", snap["suggestions"])
    assert suggestions[0] == "x" * 200  # 截断
    assert len(suggestions) == 5  # 封顶 5 条
    assert suggestions[4] == "e"
    # 非 str 标量收编(与既有上架链路 str() 口径一致)
    snap2 = build_snapshot(
        name="n",
        greeting="g",
        suggestions=[123],
        system_prompt="p",
        tools=["t1"],  # type: ignore[list-item]
    )
    assert snap2["suggestions"] == ["123"]


def test_snapshots_equal_normalized_per_field() -> None:
    """一致性判定:规范化后逐项相等——长度/空白形态差异被规范化吸收。"""
    a = build_snapshot(**_BASE)  # type: ignore[arg-type]
    b = build_snapshot(**_BASE)  # type: ignore[arg-type]
    assert snapshots_equal(a, b)
    # 非规范化入参(超长建议问题)与规范化快照等价(比较前各自规范化)
    raw = dict(_BASE, suggestions=["x" * 300 + "尾巴", *_BASE["suggestions"][1:]])
    assert not snapshots_equal(a, raw)  # type: ignore[arg-type]


def test_snapshots_unequal_on_any_field() -> None:
    """任一白名单字段不等即不一致(逐项相等,非整体哈希)。"""
    for field, changed in (
        ("name", "售后助手"),
        ("greeting", "变了"),
        ("suggestions", ["别的"]),
        ("system_prompt", "另一个人设"),
        ("tools", ["execute_sql"]),
    ):
        other = dict(_BASE, **{field: changed})
        assert not snapshots_equal(
            build_snapshot(**_BASE),  # type: ignore[arg-type]
            build_snapshot(**other),  # type: ignore[arg-type]
        ), field


def test_snapshots_equal_tolerates_missing_keys() -> None:
    """容错:jsonb 历史快照缺键按空值参与比较(不抛异常,fail-safe 形态)。"""
    a: dict[str, object] = {"name": "n", "greeting": "", "system_prompt": "p"}
    b: dict[str, object] = {"name": "n", "greeting": None, "system_prompt": "p", "tools": []}
    assert snapshots_equal(a, b)
    assert not snapshots_equal(a, {"name": "m", "greeting": "", "system_prompt": "p"})
