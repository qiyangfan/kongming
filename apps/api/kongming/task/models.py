"""task 领域模型(090):状态机/步骤账本/预算(零框架,宪法 Article II)。

原则同源:步骤状态由工具执行证据驱动(语言模型自述不构成完成证据)、
预算退出诚实、终态不复活。迁移白名单 fail-closed,非法迁移拒绝。
"""

from dataclasses import dataclass, field
from typing import Any, cast

# ── 任务状态机 ──

T_PLANNING = "PLANNING"
T_RUNNING = "RUNNING"
T_AWAITING_APPROVAL = "AWAITING_APPROVAL"
T_PAUSED = "PAUSED"
T_INTERRUPTED = "INTERRUPTED"
T_DONE = "DONE"
T_FAILED = "FAILED"
T_CANCELLED = "CANCELLED"

# 迁移白名单(缺席即非法):终态无出边(终态不复活);
# INTERRUPTED 只由启动扫描写入,恢复(→RUNNING)为显式操作。
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    T_PLANNING: frozenset({T_RUNNING, T_PAUSED, T_CANCELLED, T_FAILED, T_INTERRUPTED}),
    T_RUNNING: frozenset(
        {T_AWAITING_APPROVAL, T_PAUSED, T_DONE, T_FAILED, T_CANCELLED, T_INTERRUPTED}
    ),
    T_AWAITING_APPROVAL: frozenset({T_RUNNING, T_PAUSED, T_CANCELLED}),
    T_PAUSED: frozenset({T_RUNNING, T_CANCELLED}),
    T_INTERRUPTED: frozenset({T_RUNNING, T_CANCELLED}),
    T_DONE: frozenset(),
    T_FAILED: frozenset(),
    T_CANCELLED: frozenset(),
}

# ── 步骤状态机 ──

S_PENDING = "PENDING"
S_RUNNING = "RUNNING"
S_AWAITING_APPROVAL = "AWAITING_APPROVAL"
S_DONE = "DONE"
S_FAILED = "FAILED"
S_SKIPPED = "SKIPPED"
S_CANCELLED = "CANCELLED"

STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    S_PENDING: frozenset({S_RUNNING, S_CANCELLED}),
    S_RUNNING: frozenset({S_DONE, S_FAILED, S_SKIPPED, S_AWAITING_APPROVAL, S_CANCELLED}),
    S_AWAITING_APPROVAL: frozenset({S_PENDING, S_SKIPPED, S_CANCELLED}),
    # 批准 → 回 PENDING 待重跑(调度重下);拒绝 → SKIPPED
    S_DONE: frozenset(),
    S_FAILED: frozenset(),
    S_SKIPPED: frozenset(),
    S_CANCELLED: frozenset(),
}

# 可执行(调度下发)的步骤状态
STEP_EXECUTABLE = frozenset({S_PENDING})
# 步骤终态(无出边;任务 DONE 的前置条件=全部步骤终态)
STEP_TERMINAL = frozenset({S_DONE, S_FAILED, S_SKIPPED, S_CANCELLED})
# 任务的活动态(执行循环可推进)
TASK_ACTIVE = frozenset({T_PLANNING, T_RUNNING})
# 任务终态(无出边;迟到步骤回写只留证据,不再追加步骤/预算)
TASK_TERMINAL = frozenset({T_DONE, T_FAILED, T_CANCELLED})
# 审批过期时长(挂起超时未决 → 步骤 SKIPPED;启动扫描与审批读取双侧生效)
APPROVAL_TTL_HOURS = 24


class InvalidTransition(Exception):
    """状态迁移不在白名单(fail-closed;调用方落审计并拒绝)。"""


def transition(current: str, target: str) -> str:
    """任务状态迁移纯函数:白名单外一律拒绝。"""
    if target not in TASK_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"{current} -> {target}")
    return target


def step_transition(current: str, target: str) -> str:
    """步骤状态迁移纯函数:白名单外一律拒绝;已终态步骤不可改写。"""
    if target not in STEP_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransition(f"step {current} -> {target}")
    return target


# ── 预算(三约束:步数/token/墙钟)──

BUDGET_DEFAULTS: dict[str, int] = {"max_steps": 12, "max_tokens": 200_000, "max_seconds": 900}


def normalize_budget(config: dict[str, Any] | None) -> dict[str, int]:
    """预算配置归一(缺省补齐;非正数回退缺省)。"""
    out = dict(BUDGET_DEFAULTS)
    for k in BUDGET_DEFAULTS:
        v = (config or {}).get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            out[k] = v
    return out


def clamp_budget(normalized: dict[str, int], caps: dict[str, int]) -> dict[str, int]:
    """预算部署级钳制(090 spec「任务级配置 + 部署级上限」):
    归一后逐项取 min(任务配置, 部署上限)——部署可收紧也可放大天花板,
    但任务配置任何情况下不得超过部署上限。"""
    return {k: max(1, min(v, caps.get(k, v))) for k, v in normalized.items()}


def over_budget(used: dict[str, Any], config: dict[str, int]) -> str | None:
    """预算判定纯函数:超限返回原因摘要,未超返回 None(诚实披露依据)。"""
    if int(used.get("steps") or 0) >= config["max_steps"]:
        return "步数预算耗尽"
    if int(used.get("total_tokens") or 0) >= config["max_tokens"]:
        return "token 预算耗尽"
    if int(used.get("elapsed_ms") or 0) >= config["max_seconds"] * 1000:
        return "时长预算耗尽"
    return None


# ── 协作拓扑(091;宿主=任务步骤)──

COLLAB_DISPATCH_SERIAL = "serial"
COLLAB_DISPATCH_PARALLEL = "parallel"
COLLAB_DISPATCHES = frozenset({COLLAB_DISPATCH_SERIAL, COLLAB_DISPATCH_PARALLEL})
# 缺省 worker 数(与 agent 面编排缺省一致;钳制上限来自部署配置)
COLLAB_DEFAULT_WORKERS = 3
# 嵌套深度上限(与 agent 面编排常量同值同源语义;缺省不嵌套,协作只由任务步骤发起)
DEFAULT_MAX_DEPTH = 1
# bot_key 与 bot 域同口径(小写字母开头,小写字母/数字/下划线)
COLLAB_BOT_KEY_MAX = 64


def normalize_collab_topology(
    raw: dict[str, Any] | None, *, max_workers_cap: int
) -> dict[str, object]:
    """协作拓扑归一 + 部署级钳制(纯函数):非法值回退缺省;
    max_workers 恒在 [1, max_workers_cap] 内(部署上限单点钳制)。
    返回恒为合法载荷(库内只存归一结果)。"""
    cfg = raw if isinstance(raw, dict) else {}
    raw_workers = cfg.get("max_workers")
    workers = 0
    if isinstance(raw_workers, int) and not isinstance(raw_workers, bool):
        workers = raw_workers
    if workers < 1:
        workers = COLLAB_DEFAULT_WORKERS
    workers = min(max(1, workers), max(1, max_workers_cap))
    critic = bool(cfg.get("critic"))
    dispatch = str(cfg.get("dispatch") or COLLAB_DISPATCH_SERIAL)
    if dispatch not in COLLAB_DISPATCHES:
        dispatch = COLLAB_DISPATCH_SERIAL
    raw_bot = cfg.get("bot_key")
    bot_key = str(raw_bot).strip() if raw_bot else None
    if bot_key == "":
        bot_key = None
    return {
        "max_workers": workers,
        "critic": critic,
        "dispatch": dispatch,
        "bot_key": bot_key,
    }


def has_collab_topology(step: dict[str, Any]) -> bool:
    """步骤是否声明协作拓扑(空对象/缺列=普通单 ReAct 步)。"""
    topo = step.get("collab_topology")
    if not isinstance(topo, dict):
        return False
    return bool(cast("dict[str, object]", topo))


# ── 协作账本状态(091;与 0040 两表 CHECK 同源)──

R_RUNNING = "RUNNING"
R_DONE = "DONE"
R_FAILED = "FAILED"
R_PARTIAL = "PARTIAL"
R_CANCELLED = "CANCELLED"

W_PENDING = "PENDING"
W_RUNNING = "RUNNING"
W_DONE = "DONE"
W_FAILED = "FAILED"
W_SKIPPED = "SKIPPED"

# 收敛裁决标记(supersede 写入 verdict 列并透出查询面,语义须单源)
VERDICT_SUPERSEDED = "superseded"


def collab_run_status(verdict: str, pending_count: int) -> str:
    """聚合结论 → 协作运行状态(诚实收口):未派发清单非空 = 部分完成(PARTIAL);
    裁决 PASS → DONE;否则 FAILED(明细在 verdict/conclusions,不谎称完成)。"""
    if pending_count > 0:
        return R_PARTIAL
    return R_DONE if verdict == "PASS" else R_FAILED


# ── 载荷结构 ──


@dataclass(frozen=True)
class StepPlan:
    """计划步骤(规划段产物;require_confirm 由规划按敏感度标注)。"""

    goal: str
    require_confirm: bool = False


@dataclass(frozen=True)
class StepResult:
    """步骤执行回写(证据驱动:无证据不得置 DONE)。"""

    step_id: str
    status: str  # DONE / FAILED / SKIPPED
    summary: str = ""
    evidence: dict[str, object] = field(default_factory=dict[str, object])
    fail_reason: str | None = None
    checkpoint: dict[str, object] = field(default_factory=dict[str, object])
    tokens_used: int = 0
    appended_steps: list[StepPlan] = field(default_factory=list[StepPlan])


def validate_result_status(status: str, evidence: dict[str, object]) -> str | None:
    """证据驱动约束(纯函数):DONE 必须携带证据;返回拒绝原因或 None。
    语言模型自述不构成完成证据——evidence 至少含 trace/audit 引用之一。"""
    if status == S_DONE and not (evidence.get("trace_id") or evidence.get("audit_ref")):
        return "DONE 缺证据引用(有证据才完成)"
    if status not in (S_DONE, S_FAILED, S_SKIPPED):
        return f"回写状态非法: {status}"
    return None
