"""用户偏好闭合槽位:领域纯函数,零框架依赖。

隐式提取只允许产出槽位 patch;禁止自由文本事实。注入为可关默认值段,
不得作为 WHERE/数据范围约束。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

# 闭合槽位 schema(v1;扩槽位走 openspec 变更,不开自由文本)
SLOT_KEYS: tuple[str, ...] = (
    "preferred_datasource",
    "default_time_range",
    "metric_aliases",
    "chart_pref",
    "language",
    "timezone",
    "display_style",
)

_LIST_SLOTS = frozenset({"metric_aliases"})

_INJECT_PREFIX = (
    "以下为用户偏好默认值,可忽略;不得作为 WHERE/数据范围约束,不得静默改写业务过滤条件。"
)

_EXPLICIT_SLOT_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"默认(用|使用|走)?\s*([A-Za-z0-9_\-]+)?\s*数据源"), "preferred_datasource"),
    (re.compile(r"默认数据源"), "preferred_datasource"),
    (re.compile(r"默认(时间|范围|时间窗)"), "default_time_range"),
    (re.compile(r"结论先行|全表"), "display_style"),
    (re.compile(r"时区"), "timezone"),
    (re.compile(r"语言"), "language"),
    (re.compile(r"图表|默认图"), "chart_pref"),
    (re.compile(r"别名|口径"), "metric_aliases"),
)


@dataclass
class PrefsPatch:
    """槽位补丁:只含合法槽位键;未知键在 normalize 阶段丢弃。

    deletes=显式 null 删除指令(仅人工 PUT/显式聊天产生;隐式提取
    永不删除,values 与 deletes 均空才是空补丁)。
    """

    values: dict[str, object] = field(default_factory=dict[str, object])
    deletes: frozenset[str] = frozenset()

    def is_empty(self) -> bool:
        return not self.values and not self.deletes


def _normalize_slot_value(key: str, val: object) -> object | None:
    """单槽位值归一化:None=丢弃(空串/空列表/布尔均不落);列表封顶 20 项。"""
    if key in _LIST_SLOTS:
        items_in: list[object] = []
        if isinstance(val, str):
            s = val.strip()
            if s.startswith("["):
                try:
                    parsed = json.loads(s)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, list):
                    items_in = list(cast("list[object]", parsed))
            elif s:
                items_in = [x for x in re.split(r"[,，、|]", s) if x.strip()]
        elif isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
            items_in = list(cast("Sequence[object]", val))
        items = [str(x).strip() for x in items_in if str(x).strip()]
        return items[:20] if items else None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).strip()
    return s[:200] if s else None


def normalize_patch(raw: Mapping[str, object]) -> PrefsPatch:
    """未知键丢弃;列表槽位只收 str 列表;全空不落库。"""
    out: dict[str, object] = {}
    for key in SLOT_KEYS:
        if key not in raw:
            continue
        val = raw[key]
        if val is None:
            continue
        normalized = _normalize_slot_value(key, val)
        if normalized is not None:
            out[key] = normalized
    return PrefsPatch(values=out)


def normalize_put_patch(raw: Mapping[str, object]) -> PrefsPatch:
    """人工 PUT 归一化：显式 null=删除指令；空串/空列表=丢弃
    (维持既有空补丁归一化,不表示清空);未知键由调用方先拒绝。

    合并语义(覆盖/删除/保留)由仓储单语句 UPSERT 在库内原子完成。"""
    out: dict[str, object] = {}
    deletes: set[str] = set()
    for key in SLOT_KEYS:
        if key not in raw:
            continue
        val = raw[key]
        if val is None:
            deletes.add(key)
            continue
        normalized = _normalize_slot_value(key, val)
        if normalized is not None:
            out[key] = normalized
    return PrefsPatch(values=out, deletes=frozenset(deletes))


def reject_unknown_keys(raw: Mapping[str, object]) -> str | None:
    """PUT 全量校验:未知键拒绝(闭合 schema)。返回错误信息或 None。"""
    unknown = [k for k in raw if k not in SLOT_KEYS]
    if unknown:
        return f"未知偏好槽位: {', '.join(sorted(unknown))}"
    return None


def build_preference_hint(prefs: Mapping[str, object]) -> str:
    """注入段:零槽位返回空串;带固定前缀。"""
    lines: list[str] = []
    for key in SLOT_KEYS:
        if key not in prefs:
            continue
        val = prefs[key]
        if val is None or val == "" or val == []:
            continue
        if isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
            text = "、".join(str(x) for x in cast("Sequence[object]", val))
        else:
            text = str(val)
        lines.append(f"- {key}: {text}")
    if not lines:
        return ""
    return _INJECT_PREFIX + "\n" + "\n".join(lines)


def is_free_text_fact(text: str) -> bool:
    """隐式提取产物若含自由文本事实形态则拒绝(只允许槽位 patch)。"""
    # 业务结论/事件句:含「下降/增长/%」等结论词且非槽位值
    return bool(re.search(r"(下降|增长|上涨|下跌|环比|同比)\s*[0-9]", text))


def extract_slot_patch_from_implicit(raw: Mapping[str, object]) -> PrefsPatch:
    """隐式抽取收窄:只收槽位 patch;含自由文本事实的值丢弃。"""
    cleaned: dict[str, object] = {}
    for key, val in raw.items():
        if key not in SLOT_KEYS:
            continue
        if isinstance(val, str) and is_free_text_fact(val):
            continue
        if key in _LIST_SLOTS and isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
            items = [str(x) for x in cast("Sequence[object]", val) if not is_free_text_fact(str(x))]
            cleaned[key] = items
            continue
        cleaned[key] = val
    return normalize_patch(cleaned)


def route_explicit_intent(text: str) -> tuple[str, str | None]:
    """显式「记住」分流:槽位语义 → prefs;其余 → NOTE。

    返回 (route, slot_key|None);route ∈ {"prefs", "note"}。
    """
    for pat, slot in _EXPLICIT_SLOT_HINTS:
        if pat.search(text):
            return "prefs", slot
    return "note", None


# 显式槽位取值:捕获组1=值(路由只给键不够落库,须能取出值)
_EXPLICIT_SLOT_VALUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"默认(?:用|使用|走)?\s*([A-Za-z0-9_\-]+)\s*数据源"), "preferred_datasource"),
    (re.compile(r"默认数据源(?:是|为|[:：])?\s*([A-Za-z0-9_\-]+)"), "preferred_datasource"),
    (
        re.compile(r"默认(?:时间|范围|时间窗)(?:是|为|[:：])?\s*([^\s,，。;；]+)"),
        "default_time_range",
    ),
    (re.compile(r"(结论先行|全表)"), "display_style"),
    (re.compile(r"时区(?:是|为|[:：])?\s*([^\s,，。;；]+)"), "timezone"),
    (re.compile(r"语言(?:是|为|[:：])?\s*([^\s,，。;；]+)"), "language"),
    (re.compile(r"(?:默认)?图表(?:类型)?(?:是|为|[:：])?\s*([^\s,，。;；]+)"), "chart_pref"),
)


def parse_explicit_slot(text: str) -> tuple[str, object] | None:
    """显式记忆正文 → (槽位键, 值);取不出值返回 None(走 NOTE)。"""
    t = (text or "").strip()
    if not t:
        return None
    for pat, slot in _EXPLICIT_SLOT_VALUES:
        m = pat.search(t)
        if not m:
            continue
        val = m.group(1).strip()
        if val:
            return slot, val
    return None
