"""多步 Todo 诚实账本:状态机与收口断言纯函数。

不依赖 Web 框架或数据库。证据是工具成功结果的引用;模型自述不算证据。
预算/失败不得标 DONE。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

# 全称完成措辞(存在非 DONE 时禁止)
_ALL_DONE_PATTERNS = (
    "已全部完成",
    "全部完成",
    "均已回答",
    "已全部回答",
    "所有问题均已回答",
    "所有问题都已回答",
    "全部回答完毕",
    "已完成所有",
)

_GOAL_SPLIT = re.compile(r"[？?；;]|(?<=。)(?=[^。])")


@dataclass(frozen=True)
class TodoItem:
    """单条目标账目。DONE 必须 evidence 非空(工具成功回写)。"""

    id: str
    goal: str
    status: str = "PENDING"  # PENDING / DONE / FAILED / SKIPPED
    evidence: str = ""
    reason: str = ""


def create_todos(goals: Sequence[str]) -> list[TodoItem]:
    """按目标列表建单;空目标忽略。"""
    out: list[TodoItem] = []
    for i, goal in enumerate(goals, start=1):
        g = goal.strip()
        if g:
            out.append(TodoItem(id=f"t{i}", goal=g))
    return out


def split_goals(question: str) -> list[str] | None:
    """保守拆多子目标:≥2 个问号切分,或「和/以及/另外」连接的并列问句。

    拆不出 ≥2 条清晰目标时返回 None(单目标,不建单)。
    """
    q = (question or "").strip()
    if not q:
        return None
    parts = [p.strip(" 　\n\t") for p in _GOAL_SPLIT.split(q) if p and p.strip(" 　\n\t")]
    # 过滤过短噪声(单字/纯标点);追问短句(如「华北呢」)保留
    parts = [p for p in parts if len(p) >= 2]
    if len(parts) >= 2:
        return parts
    # 「A和B是多少」形态:按 连接词 拆成两段目标(各 ≥4 字)
    for sep in ("以及", "还有", "另外，", "另外,"):
        if sep in q:
            left, _, right = q.partition(sep)
            left, right = left.strip(" 　\n\t，,。"), right.strip(" 　\n\t，,。")
            if len(left) >= 4 and len(right) >= 4:
                return [left, right]
    return None


def apply_tool_result(
    todos: Sequence[TodoItem],
    *,
    success: bool,
    evidence: str,
    empty: bool = False,
    not_applicable: bool = False,
    reason: str = "",
    todo_id: str | None = None,
) -> list[TodoItem]:
    """工具结果回写账本:成功且有结果才 DONE;失败/空/不适用 → FAILED/SKIPPED。

    未指定 todo_id 时按序回写第一条 PENDING(顺序与建单序一致)。
    """
    if not todos:
        return []
    target_idx: int | None = None
    if todo_id:
        target_idx = next((i for i, t in enumerate(todos) if t.id == todo_id), None)
    if target_idx is None:
        target_idx = next((i for i, t in enumerate(todos) if t.status == "PENDING"), None)
    if target_idx is None:
        return list(todos)
    item = todos[target_idx]
    if item.status != "PENDING":
        return list(todos)
    if not success:
        updated = replace(item, status="FAILED", evidence="", reason=reason or "工具执行失败")
    elif not_applicable:
        updated = replace(item, status="SKIPPED", evidence="", reason=reason or "能力不适用")
    elif empty:
        updated = replace(item, status="SKIPPED", evidence="", reason=reason or "结果为空")
    else:
        updated = replace(item, status="DONE", evidence=evidence, reason="")
    out = list(todos)
    out[target_idx] = updated
    return out


def mark_pending_on_budget_exit(todos: Sequence[TodoItem]) -> list[TodoItem]:
    """预算退出钩子:保留未完成态,禁止批量改 DONE(恒等返回,契约显式)。"""
    return list(todos)


def has_open_items(todos: Sequence[TodoItem]) -> bool:
    return any(t.status != "DONE" for t in todos)


def all_done(todos: Sequence[TodoItem]) -> bool:
    return bool(todos) and all(t.status == "DONE" for t in todos)


def open_items(todos: Sequence[TodoItem]) -> list[TodoItem]:
    return [t for t in todos if t.status != "DONE"]


def render_open_disclosure(todos: Sequence[TodoItem]) -> str:
    """未完成清单披露段(存在非 DONE 时追加到收口)。"""
    opens = open_items(todos)
    if not opens:
        return ""
    lines = ["未完成事项(如实说明):"]
    for t in opens:
        detail = t.reason or "未完成"
        lines.append(f"- {t.goal}:{t.status}({detail})")
    return "\n".join(lines)


def claims_all_done(text: str) -> bool:
    """是否出现全称完成措辞。"""
    return any(p in text for p in _ALL_DONE_PATTERNS)


def assert_finalize_honest(todos: Sequence[TodoItem], text: str) -> tuple[str, str | None]:
    """收口诚实断言:存在非 DONE 时禁全称完成,并强制披露未完成清单。

    返回 (修正后文本, 违规原因|None)。零账本/全 DONE 时原文返回。
    """
    if not todos:
        return text, None
    if all_done(todos):
        return text, None
    violation: str | None = None
    body = text
    if claims_all_done(text):
        violation = "存在未完成目标却使用全称完成措辞"
        for p in _ALL_DONE_PATTERNS:
            body = body.replace(p, "部分完成")
    disclosure = render_open_disclosure(todos)
    if disclosure and disclosure not in body:
        body = f"{body.rstrip()}\n\n{disclosure}"
    return body, violation


def evidence_digest(call_id: str, tool_name: str, result_ok: bool) -> str:
    """evidence 引用:工具调用标识(模型自述不算证据)。"""
    status = "ok" if result_ok else "err"
    return f"{call_id}:{tool_name}:{status}"


@dataclass
class TodoLedger:
    """便捷包装(可选);核心逻辑在纯函数。"""

    items: list[TodoItem]

    @classmethod
    def from_goals(cls, goals: Sequence[str]) -> TodoLedger:
        return cls(items=create_todos(goals))

    def apply(
        self,
        *,
        success: bool,
        evidence: str,
        empty: bool = False,
        not_applicable: bool = False,
        reason: str = "",
        todo_id: str | None = None,
    ) -> None:
        self.items = apply_tool_result(
            self.items,
            success=success,
            evidence=evidence,
            empty=empty,
            not_applicable=not_applicable,
            reason=reason,
            todo_id=todo_id,
        )


def todos_as_dicts(todos: Sequence[TodoItem]) -> list[dict[str, str]]:
    """序列化(state 存储形态)。"""
    return [
        {
            "id": t.id,
            "goal": t.goal,
            "status": t.status,
            "evidence": t.evidence,
            "reason": t.reason,
        }
        for t in todos
    ]


def todos_from_dicts(raw: Sequence[Mapping[str, object]]) -> list[TodoItem]:
    """反序列化。"""
    return [
        TodoItem(
            id=str(item.get("id") or ""),
            goal=str(item.get("goal") or ""),
            status=str(item.get("status") or "PENDING"),
            evidence=str(item.get("evidence") or ""),
            reason=str(item.get("reason") or ""),
        )
        for item in raw
    ]
