"""定义 Bot 上架快照的字段白名单、规范化和一致性判定。新增 Bot 字段时需明确决定是否进入快照。"""

from typing import cast

# 快照字段白名单:数据源/模型等运行配置不在快照内——商店直跑仍按
# 运行闸现行口径求交
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "name",
    "greeting",
    "suggestions",
    "system_prompt",
    "tools",
)

GREETING_MAX = 500  # 与既有上架口径一致(超限在上架入口拒绝,非截断)
SUGGESTION_MAX = 200  # 单条建议问题截断上限
SUGGESTION_MAX_COUNT = 5  # 建议问题条数上限


def _as_str(v: object) -> str:
    # None 归空串(缺键/空值容错,见 snapshots_equal);其余非 str 收编为 str
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


def _as_str_list(v: object) -> list[str]:
    if not isinstance(v, list):
        return []
    return [_as_str(x) for x in cast("list[object]", v)]


def build_snapshot(
    *,
    name: object,
    greeting: object,
    suggestions: object,
    system_prompt: object,
    tools: object,
) -> dict[str, object]:
    """按五字段白名单构建并规范化上架快照。"""
    return {
        "name": _as_str(name),
        "greeting": _as_str(greeting),
        "suggestions": [
            s[:SUGGESTION_MAX] for s in _as_str_list(suggestions)[:SUGGESTION_MAX_COUNT]
        ],
        "system_prompt": _as_str(system_prompt),
        "tools": _as_str_list(tools),
    }


def snapshots_equal(a: dict[str, object], b: dict[str, object]) -> bool:
    """一致性判定(幂等上架依据):两份快照**规范化后逐项相等**。

    双侧先经同一规范化(吸收截断/类型形态差异),再按白名单逐字段比较;
    缺键按空值参与(jsonb 历史快照容错,不抛异常)。
    """

    def norm(s: dict[str, object]) -> dict[str, object]:
        return build_snapshot(
            name=s.get("name"),
            greeting=s.get("greeting"),
            suggestions=s.get("suggestions"),
            system_prompt=s.get("system_prompt"),
            tools=s.get("tools"),
        )

    na, nb = norm(a), norm(b)
    return all(na[k] == nb[k] for k in SNAPSHOT_FIELDS)
