"""ReAct 图终态整形:挂起载荷提取与恢复方式随终态携带。"""

from typing import Any, cast


def suspended_or_final(final: object, origin: str) -> dict[str, object]:
    """图内挂起 → 确认载荷;否则终态 dict + 恢复方式(fresh/snapshot)。"""
    final_d = cast("dict[str, object]", final)
    interrupts = final_d.get("__interrupt__")
    if interrupts:
        payload: dict[str, object] = {}
        first = cast("Any", interrupts[0]) if isinstance(interrupts, list) else None
        value = getattr(first, "value", None)
        if isinstance(value, dict):
            payload = cast("dict[str, object]", value)
        return {
            "suspended": {
                "confirm_id": str(payload.get("confirm_id") or ""),
                "tool_name": str(payload.get("tool_name") or ""),
                "summary": str(payload.get("summary") or ""),
            }
        }
    out = dict(final_d)
    out["resume_origin"] = origin
    return out
