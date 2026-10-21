"""evalj 领域模型与纯函数:不依赖 Web 框架或数据库。

judge 运行记录的领域侧:对象类型/verdict 取值域单源、run 汇总纯函数、
跨基线对比纯函数(红线判定由 harness 门禁消费,本模块只出结构化事实)。
"""

from dataclasses import dataclass, field
from datetime import datetime

# ── 取值域单源(DDL CHECK 同步)──
OBJECT_REPORT = "report"
OBJECT_TASK = "task"
OBJECT_COLLAB = "collab"
OBJECT_ANSWER = "answer"
OBJECT_ROUNDTABLE = "roundtable"  # 圆桌运行(独立 rubric 维度族)
OBJECT_TYPES: tuple[str, ...] = (
    OBJECT_REPORT,
    OBJECT_TASK,
    OBJECT_COLLAB,
    OBJECT_ANSWER,
    OBJECT_ROUNDTABLE,
)

VERDICT_PASS = "PASS"  # noqa: S105  领域取值域常量非凭证
VERDICT_BORDERLINE = "BORDERLINE"
VERDICT_FAIL = "FAIL"
VERDICT_ERROR = "ERROR"  # LLM 失败/超时/输出不可解析:计分母不计分子(spec)

RUN_RUNNING = "RUNNING"
RUN_COMPLETED = "COMPLETED"
RUN_FAILED = "FAILED"

# 基线红线阈值(与 eval 域劣化红线同口径:5pp)
REDLINE_DROP_PP = 5.0


@dataclass(frozen=True)
class JudgeObject:
    """评审对象(只读快照):类型 + 持久层 id + 人读标识 + 评审文本。"""

    object_type: str
    object_id: str
    object_ref: str  # 人读标识(报告标题/任务目标/问句),落 object_ref 列
    text: str  # 送 judge 的产出原文(已截断装配)


@dataclass(frozen=True)
class JudgeOutcome:
    """单对象评审收口:verdict 三档或 ERROR;quotes=产出原文摘录。"""

    object_id: str
    verdict: str
    dimensions: dict[str, int] = field(default_factory=dict[str, int])
    quotes: list[str] = field(default_factory=list[str])
    raw_output: str = ""
    error: str = ""


@dataclass(frozen=True)
class RunSummary:
    """run 汇总(纯函数产物):ERROR 计分母不计分子,BORDERLINE 同。"""

    total: int
    passed: int
    borderline: int
    failed: int
    errored: int

    @property
    def pass_rate(self) -> float | None:
        if self.total == 0:
            return None
        return self.passed / self.total


def summarize(outcomes: list[JudgeOutcome]) -> RunSummary:
    """逐对象收口 → run 汇总(纯函数;BORDERLINE/ERROR 都计入分母不计分子)。"""
    return RunSummary(
        total=len(outcomes),
        passed=sum(1 for o in outcomes if o.verdict == VERDICT_PASS),
        borderline=sum(1 for o in outcomes if o.verdict == VERDICT_BORDERLINE),
        failed=sum(1 for o in outcomes if o.verdict == VERDICT_FAIL),
        errored=sum(1 for o in outcomes if o.verdict == VERDICT_ERROR),
    )


@dataclass(frozen=True)
class BaselineCompare:
    """相邻两次 run(同类型)的基线对比事实:是否触发红线 + 面向人的标注。

    跨 rubric 版本 → comparable=False(显式标注,不套红线);
    无前基线 → 首轮只记录;
    同版本劣化 ≥5pp → red_line=True(须逐对象溯源)。
    """

    comparable: bool
    red_line: bool
    note: str
    previous_rate: float | None
    current_rate: float


def compare_to_baseline(
    current_rate: float,
    current_rubric_version: str,
    previous_rate: float | None,
    previous_rubric_version: str | None,
    *,
    redline_drop_pp: float = REDLINE_DROP_PP,
) -> BaselineCompare:
    """judge 通过率基线对比(纯函数;与 eval 域「劣化 ≥5pp 红线」同口径)。

    - 前基线缺失 → 首轮记录,不判红线;
    - rubric 版本不同 → 不可比,显式标注两侧版本,不静默套红线;
    - 同版本 → 劣化(下降)≥ redline_drop_pp 个百分点触发红线。
    """
    if previous_rate is None or previous_rubric_version is None:
        return BaselineCompare(
            comparable=False,
            red_line=False,
            note="judge 首轮基线(无前值,仅记录)",
            previous_rate=None,
            current_rate=current_rate,
        )
    if previous_rubric_version != current_rubric_version:
        return BaselineCompare(
            comparable=False,
            red_line=False,
            note=(
                f"跨 rubric 版本,分数不可直接比较"
                f"(本次 {current_rubric_version} vs 基线 {previous_rubric_version})"
            ),
            previous_rate=previous_rate,
            current_rate=current_rate,
        )
    drop_pp = (previous_rate - current_rate) * 100.0
    if drop_pp >= redline_drop_pp:
        return BaselineCompare(
            comparable=True,
            red_line=True,
            note=(
                f"judge 通过率较前基线劣化 {drop_pp:.1f}pp"
                f"(≥{redline_drop_pp:.0f}pp 红线),须逐对象溯源"
            ),
            previous_rate=previous_rate,
            current_rate=current_rate,
        )
    return BaselineCompare(
        comparable=True,
        red_line=False,
        note=f"较前基线 {drop_pp:+.1f}pp(红线 {redline_drop_pp:.0f}pp)",
        previous_rate=previous_rate,
        current_rate=current_rate,
    )


@dataclass(frozen=True)
class JudgeRunRecord:
    """judge_run 读侧视图(基线对比查询产物)。"""

    id: str
    object_type: str
    rubric_version: str
    prompt_fingerprint: str
    status: str
    total_objects: int
    pass_count: int
    pass_rate: float | None
    created_at: datetime
