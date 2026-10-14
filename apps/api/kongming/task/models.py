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


# ── 协作拓扑(091;宿主=任务步骤;115 增圆桌形态)──

COLLAB_DISPATCH_SERIAL = "serial"
COLLAB_DISPATCH_PARALLEL = "parallel"
COLLAB_DISPATCHES = frozenset({COLLAB_DISPATCH_SERIAL, COLLAB_DISPATCH_PARALLEL})
# 拓扑形态(115):decomposition=091 分工编排(缺省,存量行缺 kind 即此);
# roundtable=对等圆桌(黑板+轮次,显式 opt-in)
COLLAB_KIND_DECOMPOSITION = "decomposition"
COLLAB_KIND_ROUNDTABLE = "roundtable"
COLLAB_KINDS = frozenset({COLLAB_KIND_DECOMPOSITION, COLLAB_KIND_ROUNDTABLE})
# 缺省 worker 数(与 agent 面编排缺省一致;钳制上限来自部署配置)
COLLAB_DEFAULT_WORKERS = 3
# 圆桌轮次上限缺省(与 agent 面编排常量同值同源;部署 cap 钳制)
COLLAB_DEFAULT_ROUNDS = 3
# 圆桌参与者名册下限(二至部署上限;低于下限的声明回退缺省名册)
COLLAB_MIN_PARTICIPANTS = 2
# 内置角色章程取值域(圆桌参与者的声明角色;名册外角色声明逐条剔除)
COLLAB_ROLES = frozenset({"analyst", "skeptic", "auditor"})
# 缺省名册(声明未给出合法名册时的回退):轻形态 2 人——2026-10-14 实测裁决:
# 3 人×2 轮 ≈100k token 顶满任务编排硬顶,且专职审计者抬高单轮成本、
# 压低轮内共识率;auditor 仍在取值域内可显式声明(重形态 opt-in)。
COLLAB_DEFAULT_ROSTER: tuple[dict[str, object], ...] = (
    {"role": "analyst"},
    {"role": "skeptic"},
)
# 嵌套深度上限(与 agent 面编排常量同值同源语义;缺省不嵌套,协作只由任务步骤发起)
DEFAULT_MAX_DEPTH = 1
# bot_key 与 bot 域同口径(小写字母开头,小写字母/数字/下划线)
COLLAB_BOT_KEY_MAX = 64


def _norm_bot_key(raw: object) -> str | None:
    """bot_key 归一(空串/空白 → None;与 091 单绑定口径一致)。"""
    key = str(raw).strip() if raw else ""
    return key or None


def normalize_collab_topology(
    raw: dict[str, Any] | None,
    *,
    max_workers_cap: int,
    max_rounds_cap: int = COLLAB_DEFAULT_ROUNDS,
) -> dict[str, object]:
    """协作拓扑归一 + 部署级钳制(纯函数):非法值回退缺省;
    分工形态 max_workers 恒在 [1, max_workers_cap];圆桌形态参与者恒在
    [2, max(2, max_workers_cap)]、max_rounds 恒在 [1, max_rounds_cap]
    (部署上限单点钳制)。返回恒为合法载荷(库内只存归一结果)。"""
    cfg = raw if isinstance(raw, dict) else {}
    kind = str(cfg.get("kind") or COLLAB_KIND_DECOMPOSITION)
    if kind not in COLLAB_KINDS:
        kind = COLLAB_KIND_DECOMPOSITION
    if kind == COLLAB_KIND_ROUNDTABLE:
        roster: list[dict[str, object]] = []
        raw_parts = cfg.get("participants")
        for item in cast("list[object]", raw_parts if isinstance(raw_parts, list) else []):
            if not isinstance(item, dict):
                continue
            entry = cast("dict[str, object]", item)
            role = str(entry.get("role") or "")
            if role not in COLLAB_ROLES:
                continue
            roster.append({"role": role, "bot_key": _norm_bot_key(entry.get("bot_key"))})
        # 名册去重(同角色重复声明裁到首条;角色即参与者的确定性标识)
        seen: set[str] = set()
        deduped: list[dict[str, object]] = []
        for p in roster:
            role = str(p["role"])
            if role in seen:
                continue
            seen.add(role)
            deduped.append(p)
        roster = deduped
        cap = max(COLLAB_MIN_PARTICIPANTS, max(1, max_workers_cap))
        if len(roster) > cap:
            roster = roster[:cap]
        if len(roster) < COLLAB_MIN_PARTICIPANTS:
            roster = [dict(p) for p in COLLAB_DEFAULT_ROSTER][:cap]
        raw_rounds = cfg.get("max_rounds")
        rounds = (
            raw_rounds if isinstance(raw_rounds, int) and not isinstance(raw_rounds, bool) else 0
        )
        if rounds < 1:
            rounds = COLLAB_DEFAULT_ROUNDS
        rounds = min(max(1, rounds), max(1, max_rounds_cap))
        return {"kind": kind, "max_rounds": rounds, "participants": roster}
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
    return {
        "kind": kind,
        "max_workers": workers,
        "critic": critic,
        "dispatch": dispatch,
        "bot_key": _norm_bot_key(cfg.get("bot_key")),
    }


def has_collab_topology(step: dict[str, Any]) -> bool:
    """步骤是否声明协作拓扑(空对象/缺列=普通单 ReAct 步)。"""
    topo = step.get("collab_topology")
    if not isinstance(topo, dict):
        return False
    return bool(cast("dict[str, object]", topo))


def roundtable_roster(topo: dict[str, Any]) -> list[dict[str, object]]:
    """圆桌名册读取(缺 kind=分工形态 → 空名册;运行期名册锁定以此为准)。"""
    if str(topo.get("kind") or "") != COLLAB_KIND_ROUNDTABLE:
        return []
    parts = topo.get("participants")
    if not isinstance(parts, list):
        return []
    return [dict(p) for p in cast("list[dict[str, object]]", parts)]


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

# ── 圆桌轮次账本(115;与 0054 turn 表 CHECK 同源)──

TURN_SPEECH = "speech"
# 预留值(design:一次建全已规划列,0054 CHECK 同域):当前编排的查证内嵌于
# 发言轮,agent 面不产该 kind;留作独立查证轮的演进位,勿删除
TURN_VERIFICATION = "verification"
TURN_ABSTAIN = "abstain"
TURN_VERDICT = "verdict"
TURN_KINDS = frozenset({TURN_SPEECH, TURN_VERIFICATION, TURN_ABSTAIN, TURN_VERDICT})
# 主席判定行的参与者序号(主席非名册成员,不占 worker 行;0 号为判定保留位)
CHAIR_WORKER_NO = 0


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
