"""judge run 编排(092,任务 2.3):显式评审范围(类型×对象集)→ 逐对象独立
收口 → run 汇总落库(design D3/D4)。

单对象失败(LLM 失败/超时/输出不可解析)不中断 run:保留原始输出、显式
ERROR;解析失败同 prompt 重试一次再收口。通过率口径=PASS/总对象
(BORDERLINE/ERROR 计分母不计分子,kongming.evalj.models.summarize)。
"""
# pyright: reportUnknownArgumentType=false, reportPrivateUsage=false

import logging
import re
from typing import Protocol, cast

from evals.judge.client import JudgeLlmError, parse_json_object
from evals.judge.rubrics import (
    _RUBRICS,
    build_judge_prompt,
    judge_prompt_fingerprint,
    rubric_version,
)
from kongming.evalj.models import (
    OBJECT_TYPES,
    RUN_COMPLETED,
    RUN_FAILED,
    VERDICT_BORDERLINE,
    VERDICT_ERROR,
    VERDICT_FAIL,
    VERDICT_PASS,
    JudgeObject,
    JudgeOutcome,
    RunSummary,
    summarize,
)

log = logging.getLogger(__name__)

_MAX_TEXT = 6000  # 送 judge 的产出正文截断(与 objects 取数截断一致,双保险)


class JudgeChat(Protocol):
    """judge 评审 LLM 端口(生产=JudgeLlm;测试注入脚本替身)。"""

    async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str: ...


class RunStore(Protocol):
    """评审产出写入端口(生产=SqlJudgeRepo;测试注入内存版)。"""

    async def create_run(
        self, *, object_type: str, rubric_version: str, prompt_fingerprint: str, note: str = ""
    ) -> str: ...

    async def save_result(self, run_id: str, outcome: JudgeOutcome, object_ref: str) -> None: ...

    async def finish_run(self, run_id: str, summary: RunSummary, status: str) -> None: ...


class JudgeParseError(RuntimeError):
    """judge 输出不可解析(重试后仍失败 → 收口 ERROR 的依据)。"""


_DIM_BOUND_RE = re.compile(r"^1-(\d+)")  # 分档描述前缀「1-N:」(rubrics 声明形态)


def _dim_bound(desc: str) -> int:
    """维度分档上限(从描述前缀解析;声明缺失保守按 v1 口径 1..5)。"""
    m = _DIM_BOUND_RE.match(desc.strip())
    return int(m.group(1)) if m else 5


def parse_verdict(raw: str, valid_dimensions: dict[str, str]) -> JudgeOutcome:
    """judge 输出解析(纯函数):verdict 越域/维度分档缺失或非整数/quotes
    非数组 → 不可解析。dimensions 只收已声明维度(评审诚实,不补缺)。"""
    parsed = parse_json_object(raw)
    if parsed is None:
        raise JudgeParseError("输出非 JSON 对象")
    verdict = str(parsed.get("verdict") or "").strip().upper()
    if verdict not in (VERDICT_PASS, VERDICT_BORDERLINE, VERDICT_FAIL):
        raise JudgeParseError(f"verdict 越域: {verdict!r}")
    raw_dims_obj = parsed.get("dimensions")
    if not isinstance(raw_dims_obj, dict):
        raise JudgeParseError("dimensions 缺失或非对象")
    raw_dims = cast("dict[str, object]", raw_dims_obj)
    dims: dict[str, int] = {}
    for name in valid_dimensions:
        value = cast("object", raw_dims.get(name))
        if isinstance(value, bool) or not isinstance(value, int):
            raise JudgeParseError(f"维度 {name} 分档缺失或非整数: {value!r}")
        bound = _dim_bound(valid_dimensions[name])
        if not 1 <= value <= bound:
            # 越档分数(99/-3)照单全收会污染加权分(加权值>上限或为负),
            # 与「维度分档」评审口径直接矛盾——按不可解析处理(触发重试)
            raise JudgeParseError(f"维度 {name} 分档越界(须 1..{bound}): {value!r}")
        dims[name] = value
    raw_quotes = parsed.get("quotes")
    if not isinstance(raw_quotes, list):
        raise JudgeParseError("quotes 缺失或非数组")
    quotes = [str(q) for q in cast("list[object]", raw_quotes) if isinstance(q, str) and q.strip()]
    return JudgeOutcome(
        object_id="", verdict=verdict, dimensions=dims, quotes=quotes, raw_output=raw
    )


async def judge_one(llm: JudgeChat, obj: JudgeObject) -> JudgeOutcome:
    """单对象评审(独立收口):解析/调用失败重试一次,再失败=ERROR+原始输出。"""
    system, user = build_judge_prompt(obj.object_type, obj.object_ref, obj.text[:_MAX_TEXT])
    valid_dims = _RUBRICS[obj.object_type].dimensions
    raw = ""
    last_error = ""
    for attempt in (1, 2):
        try:
            raw = await llm.chat(system, user)
            parsed = parse_verdict(raw, valid_dims)
            return JudgeOutcome(
                object_id=obj.object_id,
                verdict=parsed.verdict,
                dimensions=parsed.dimensions,
                quotes=parsed.quotes,
                raw_output=raw,
            )
        except JudgeParseError as e:
            last_error = str(e)
            log.warning("judge 输出不可解析(第 %d 次): %s", attempt, e)
        except JudgeLlmError as e:
            last_error = str(e)
            log.warning("judge LLM 调用失败(第 %d 次): %s", attempt, e)
    return JudgeOutcome(
        object_id=obj.object_id,
        verdict=VERDICT_ERROR,
        raw_output=raw,
        error=f"评审失败(重试后): {last_error}",
    )


async def run_judge(
    llm: JudgeChat,
    repo: RunStore,
    object_type: str,
    objects: list[JudgeObject],
    *,
    note: str = "",
) -> tuple[str, list[JudgeOutcome], RunSummary]:
    """一次 judge run:开 run → 逐对象独立收口 → 汇总落库。
    返回 (run_id, 逐对象结果, run 汇总)。空对象集=合法空 run(0/0)。
    全对象 ERROR 收口 RUN_FAILED(评审未产生任何有效判定,如实标记);
    其余(含部分失败)COMPLETED——失败对象已逐个显式 ERROR,通过率自然
    反映,门禁侧照样可溯源。"""
    if object_type not in OBJECT_TYPES:
        raise ValueError(f"未知评审对象类型: {object_type}")
    version = rubric_version(object_type)
    fingerprint = judge_prompt_fingerprint()
    run_id = await repo.create_run(
        object_type=object_type,
        rubric_version=version,
        prompt_fingerprint=fingerprint,
        note=note,
    )
    outcomes: list[JudgeOutcome] = []
    for obj in objects:
        outcome = await judge_one(llm, obj)
        outcomes.append(outcome)
        await repo.save_result(run_id, outcome, obj.object_ref)
    summary = summarize(outcomes)
    status = RUN_COMPLETED
    if summary.total > 0 and summary.errored == summary.total:
        status = RUN_FAILED
    await repo.finish_run(run_id, summary, status)
    return run_id, outcomes, summary
