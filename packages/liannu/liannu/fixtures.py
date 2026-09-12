"""对拍 fixture:冻结期望集的模式定义与回放。

fixture 由消费方(基于其内置实现与既有测试资产/evals gold)导出冻结,
本模块定义 schema 常量与回放比对;规则移植漂移在回放处即红。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from liannu.artifact import GuardArtifact, GuardError

SCHEMA_VERSION = 1

# 期望键(check 三闸 + 行级改写结构)
EXPECT_KEYS = (
    "check",
    "check_schema_scope",
    "check_reserved_tables",
    "main_table_ref",
    "apply_filters",
)


@dataclass(frozen=True)
class Mismatch:
    """一处对拍不一致(结构完整:用例标识 + 检查名 + 双方判定)。"""

    case_id: str
    check_name: str
    builtin: object
    engine: object


def load_fixtures(path: str | Path) -> dict[str, Any]:
    data: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"fixture schema_version 须为 {SCHEMA_VERSION}: {path}")
    return data


def _verdict_tuple(v: Any) -> tuple[bool, str | None]:
    return bool(v["passed"]), v.get("reason")


def replay(data: dict[str, Any]) -> list[Mismatch]:
    """回放冻结期望集,返回不一致列表(空 = 全绿)。

    depth_cases 仅钉引擎受控行为(DEPTH_EXCEEDED),无内置对拍形态。
    """
    mismatches: list[Mismatch] = []
    for case in data.get("cases", []):
        cid: str = case["id"]
        art = GuardArtifact.parse(case["sql"])
        expect: dict[str, Any] = case["expect"]
        for key in ("check", "check_schema_scope", "check_reserved_tables"):
            exp_v = expect.get(key)
            if exp_v is None:
                continue  # 该用例未冻结此闸(如未给 allowed_schemas)
            if key == "check_schema_scope":
                allowed = set(case["allowed_schemas"])
                got = art.check_schema_scope(allowed)
            else:
                got = art.check_firewall() if key == "check" else art.check_reserved_tables()
            got_tuple = (got.passed, got.reason)
            if _verdict_tuple(exp_v) != got_tuple:
                mismatches.append(Mismatch(cid, key, _verdict_tuple(exp_v), got_tuple))
        mt_ref = expect.get("main_table_ref")
        if mt_ref is not None:
            exp_ref = mt_ref["ref"]  # 导出方包装:{"ref": [table, schema]|None}
            got_ref = art.main_table_ref()
            got_list = list(got_ref) if got_ref is not None else None
            if got_list != exp_ref:
                mismatches.append(Mismatch(cid, "main_table_ref", exp_ref, got_list))
        for flt in expect.get("apply_filters", []):
            try:
                got_sql, got_params = art.apply_scope_filter(
                    filter_type=flt["filter_type"],
                    column=flt.get("column"),
                    values=flt.get("values", []),
                )
            except GuardError as e:
                mismatches.append(Mismatch(cid, "apply_scope_filter", "ok", f"GuardError: {e}"))
                continue
            if got_sql != flt["sql"] or got_params != flt["params"]:
                mismatches.append(
                    Mismatch(
                        cid,
                        "apply_scope_filter",
                        (flt["sql"], flt["params"]),
                        (got_sql, got_params),
                    )
                )
    for dcase in data.get("depth_cases", []):
        max_depth = dcase.get("max_depth")
        art = (
            GuardArtifact.parse(dcase["sql"])
            if max_depth is None
            else GuardArtifact.parse(dcase["sql"], max_depth=max_depth)
        )
        v = art.check_firewall()
        prefix = dcase.get("expect_reason_prefix", "DEPTH_EXCEEDED")
        if v.passed or not (v.reason or "").startswith(prefix):
            mismatches.append(Mismatch(dcase["id"], "depth", f"{prefix}*", (v.passed, v.reason)))
    return mismatches
