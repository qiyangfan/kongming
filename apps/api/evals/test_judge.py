"""judge 抽检层(092):定时门禁在 Execution Match smoke 之外对非确定性
产出抽样跑 judge(ai-eval delta「定时门禁 judge 抽检层」)。

- 抽样:按对象类型分层,每类型每轮 ≥PER_TYPE 个(存量不足取全量),
  取最新产出;时间窗=「全量最新」(design Open Questions 裁定形态)。
- 通过率落 judge_run(库)+ 本行输出(artifacts 可见),与上次基线可比;
  同 rubric 版本劣化 ≥5pp → 门禁失败(逐对象溯源信息随失败输出)。
- 类型空缺(如当前环境无 DONE 任务/协作 run)→ 如实记录,不计失败。
- judge 凭证缺失 → 显式失败(client 抛 JudgeLlmError),不静默跳过。
"""
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false, reportPrivateUsage=false

import os
from typing import cast

import pytest
from conftest import _login

from evals.judge.client import JudgeLlm
from evals.judge.objects import ObjectClient, gather_objects
from evals.judge.rubrics import rubric_version
from evals.judge.runner import run_judge
from kongming.db import get_sessionmaker
from kongming.evalj.models import (
    OBJECT_TYPES,
    JudgeOutcome,
    RunSummary,
    compare_to_baseline,
)
from kongming.evalj.repository import JudgeRunRecord, SqlJudgeRepo

PER_TYPE = 3  # 每类型每轮抽样下限(spec:≥3/类型,不足全量)
API_BASE = os.getenv("KM_EVAL_API_BASE", "http://127.0.0.1:8000")
TENANT = "default"


def _outcome_detail(outcomes: list[JudgeOutcome]) -> str:
    """逐对象溯源明细(红线失败时随断言输出)。"""
    lines: list[str] = []
    for o in outcomes:
        head = f"[{o.verdict}] {o.object_id}"
        if o.verdict == "ERROR":
            lines.append(f"{head} error={o.error}")
        else:
            dims = ",".join(f"{k}={v}" for k, v in o.dimensions.items())
            quotes = " | ".join(o.quotes[:2])
            lines.append(f"{head} {dims} 摘录:{quotes[:120]}")
    return "\n".join(lines)


class _ShortTxnRunStore:
    """每次调用独立短事务的 RunStore 适配器(生产=SqlJudgeRepo 单 session)。
    judge 门禁单对象评审可长达分钟级(LLM 超时×重试),若整个 run 包在一个
    未提交事务里,LLM 网络等待全部变成 idle-in-transaction——逐调用开短事务
    即时提交,LLM 调用落在事务外,且已完成对象的结果崩溃不丢(部分进度持久)。"""

    def __init__(self) -> None:
        self._maker = get_sessionmaker()

    async def _tx(self, method: str, *args: object, **kw: object) -> object:
        async with self._maker() as session:
            out = await getattr(SqlJudgeRepo(session), method)(*args, **kw)
            await session.commit()
            return out

    async def create_run(
        self, *, object_type: str, rubric_version: str, prompt_fingerprint: str, note: str = ""
    ) -> str:
        return cast(
            "str",
            await self._tx(
                "create_run",
                object_type=object_type,
                rubric_version=rubric_version,
                prompt_fingerprint=prompt_fingerprint,
                note=note,
            ),
        )

    async def save_result(self, run_id: str, outcome: JudgeOutcome, object_ref: str) -> None:
        await self._tx("save_result", run_id, outcome, object_ref)

    async def finish_run(self, run_id: str, summary: RunSummary, status: str) -> None:
        await self._tx("finish_run", run_id, summary, status)

    async def latest_completed(
        self, *, object_type: str, exclude_run_id: str
    ) -> JudgeRunRecord | None:
        return cast(
            "JudgeRunRecord | None",
            await self._tx(
                "latest_completed", object_type=object_type, exclude_run_id=exclude_run_id
            ),
        )


@pytest.mark.smoke
@pytest.mark.judge
async def test_judge_gate_sampling() -> None:
    """门禁 judge 抽检层:四类型分层抽样 → run 落库 → 基线对比红线判定。"""
    token = await _login()
    client = ObjectClient(API_BASE, token)
    per_type = await gather_objects(client, TENANT, PER_TYPE)
    if not any(per_type.values()):
        pytest.skip("四类评审对象全部空缺(环境无产出),judge 层如实记录")
    llm = JudgeLlm()  # 凭证缺失在此显式失败(spec 口径)
    store = _ShortTxnRunStore()  # LLM 调用不跨事务(短事务逐调用提交)
    failures: list[str] = []
    for object_type in OBJECT_TYPES:
        objects = per_type.get(object_type) or []
        if not objects:
            print(f"[judge:{object_type}] 类型空缺,如实记录(不计失败)")
            continue
        run_id, outcomes, summary = await run_judge(
            llm, store, object_type, objects, note="scheduled-gate sampling"
        )
        previous = await store.latest_completed(object_type=object_type, exclude_run_id=run_id)
        rate = summary.pass_rate or 0.0
        prev_rate = previous.pass_rate if previous is not None else None
        prev_version = previous.rubric_version if previous is not None else None
        compare = compare_to_baseline(
            current_rate=rate,
            current_rubric_version=rubric_version(object_type),
            previous_rate=prev_rate,
            previous_rubric_version=prev_version,
        )
        print(
            f"[judge:{object_type}] run={run_id} 通过率 {summary.passed}/{summary.total}"
            f" ({rate:.0%}) borderline={summary.borderline} error={summary.errored}"
            f" | {compare.note}",
            flush=True,
        )
        if compare.red_line:
            failures.append(
                f"[{object_type}] {compare.note}\nrun={run_id}\n{_outcome_detail(outcomes)}"
            )
    assert not failures, "judge 门禁红线触发:\n" + "\n\n".join(failures)
