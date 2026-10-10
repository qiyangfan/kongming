"""evalj 域与 judge 编排单测(092):默认门禁可跑(不依赖评估环境)。"""
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false, reportPrivateUsage=false

import pytest

from kongming.evalj.models import (
    OBJECT_ANSWER,
    OBJECT_COLLAB,
    OBJECT_REPORT,
    OBJECT_TASK,
    BaselineCompare,
    JudgeOutcome,
    RunSummary,
    compare_to_baseline,
    summarize,
)


def _o(verdict: str, oid: str = "x") -> JudgeOutcome:
    return JudgeOutcome(object_id=oid, verdict=verdict)


# ── summarize(通过率口径:BORDERLINE/ERROR 计分母不计分子)──


def test_summarize_counts_all_in_denominator() -> None:
    s = summarize([_o("PASS"), _o("BORDERLINE"), _o("FAIL"), _o("ERROR"), _o("PASS")])
    assert s == RunSummary(total=5, passed=2, borderline=1, failed=1, errored=1)
    assert s.pass_rate == 0.4


def test_summarize_empty_run() -> None:
    assert summarize([]).pass_rate is None


# ── compare_to_baseline(基线红线口径)──


def test_baseline_first_run_records_only() -> None:
    c = compare_to_baseline(0.9, "v1", None, None)
    assert not c.red_line and not c.comparable and "首轮" in c.note


def test_baseline_cross_version_not_comparable() -> None:
    c = compare_to_baseline(0.5, "v2", 0.9, "v1")
    assert not c.red_line and not c.comparable
    assert "v2" in c.note and "v1" in c.note


def test_baseline_same_version_drop_triggers_redline() -> None:
    """spec 场景:90% → 82%(同 v1)= -8pp ≥ 5pp 红线。"""
    c = compare_to_baseline(0.82, "v1", 0.90, "v1")
    assert c.comparable and c.red_line and "8.0pp" in c.note


def test_baseline_same_version_small_drift_no_redline() -> None:
    c = compare_to_baseline(0.87, "v1", 0.90, "v1")
    assert c.comparable and not c.red_line  # -3pp 采样波动余量内


def test_baseline_improvement_never_redline() -> None:
    c = compare_to_baseline(1.0, "v1", 0.8, "v1")
    assert not c.red_line and c.current_rate > (c.previous_rate or 0)


# ── rubric 纪律:四类齐备、版本随代码、指纹稳定 ──


def test_rubrics_cover_four_object_types() -> None:
    from evals.judge.rubrics import _RUBRICS, judge_prompt_fingerprint, rubric_version

    for ot in (OBJECT_REPORT, OBJECT_TASK, OBJECT_COLLAB, OBJECT_ANSWER):
        assert ot in _RUBRICS
        assert rubric_version(ot)
        assert _RUBRICS[ot].dimensions
        assert _RUBRICS[ot].weights
    fp1, fp2 = judge_prompt_fingerprint(), judge_prompt_fingerprint()
    assert fp1 == fp2 and len(fp1) == 8


def test_rubric_weighted_score_ignores_undeclared() -> None:
    from evals.judge.rubrics import OBJECT_REPORT, rubric_weighted_score

    score = rubric_weighted_score({"结构完整性": 4, "数据准确性": 2, "意外维度": 5}, OBJECT_REPORT)
    assert score is not None
    # 结构 4×0.25 + 数据 2×0.35 = 1.7 / 0.6 ≈ 2.83(意外维度不计权)
    assert abs(score - 1.7 / 0.6) < 1e-9
    assert rubric_weighted_score({}, OBJECT_REPORT) is None


# ── parse_verdict(结构化解析口径,design D3)──


def test_parse_verdict_accepts_wellformed() -> None:
    from evals.judge.runner import parse_verdict

    raw = (
        '{"verdict":"PASS","dimensions":{"结构完整性":4,"数据准确性":3},'
        '"quotes":["数据一致性引用"]}'
    )
    o = parse_verdict(raw, {"结构完整性": "1-5", "数据准确性": "1-5"})
    assert o.verdict == "PASS"
    assert o.dimensions == {"结构完整性": 4, "数据准确性": 3}
    assert o.quotes == ["数据一致性引用"]


def test_parse_verdict_rejects_malformed() -> None:
    import pytest as _pytest

    from evals.judge.runner import JudgeParseError, parse_verdict

    dims = {"结构完整性": "1-5", "数据准确性": "1-5"}
    with _pytest.raises(JudgeParseError):
        parse_verdict("不是 JSON", dims)
    with _pytest.raises(JudgeParseError):
        parse_verdict('{"verdict":"EXCELLENT","dimensions":{},"quotes":[]}', dims)
    with _pytest.raises(JudgeParseError):
        parse_verdict('{"verdict":"PASS","dimensions":{"结构完整性":4},"quotes":[]}', dims)
    with _pytest.raises(JudgeParseError):
        parse_verdict(
            '{"verdict":"PASS","dimensions":{"结构完整性":"高","数据准确性":3},"quotes":[]}',
            dims,
        )
    with _pytest.raises(JudgeParseError):
        parse_verdict('{"verdict":"PASS","dimensions":{"结构完整性":4,"数据准确性":3}}', dims)
    for bad in ("0", "-3", "6", "99"):
        with _pytest.raises(JudgeParseError):
            # 越档分数(1..5 之外)照单全收会污染加权分——按不可解析处理
            parse_verdict(
                f'{{"verdict":"PASS","dimensions":{{"结构完整性":{bad},"数据准确性":3}},"quotes":[]}}',
                dims,
            )
    with _pytest.raises(JudgeParseError):
        # 分档上限随 rubric 声明(「1-N:」前缀)收窄,不写死 5
        parse_verdict(
            '{"verdict":"PASS","dimensions":{"结构完整性":3,"数据准确性":3},"quotes":[]}',
            {"结构完整性": "1-2", "数据准确性": "1-5"},
        )


# ── judge_one 逐对象独立收口(LLM 替身直调)──


async def test_judge_one_retries_once_then_error() -> None:
    """解析失败重试一次;再失败=ERROR 并保留原始输出(不静默跳过)。"""
    from evals.judge.client import JudgeLlmError
    from evals.judge.runner import judge_one
    from kongming.evalj.models import OBJECT_REPORT, JudgeObject

    class _FlakyLlm:
        def __init__(self, replies: list[str]) -> None:
            self.replies = list(replies)

        async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
            if not self.replies:
                raise JudgeLlmError("下游不可达")
            return self.replies.pop(0)

    obj = JudgeObject(object_type=OBJECT_REPORT, object_id="r1", object_ref="报告A", text="内容")
    # 两次都不可解析 → ERROR + 原始输出保留
    outcome = await judge_one(_FlakyLlm(["垃圾输出", "还是垃圾"]), obj)
    assert outcome.verdict == "ERROR" and "还是垃圾" in outcome.raw_output  # 保留最后一次尝试
    # 第一次失败第二次成功 → 正常收口
    good = (
        '{"verdict":"FAIL","dimensions":{"结构完整性":2,"数据准确性":2,'
        '"洞察价值":1,"表述质量":2},"quotes":[]}'
    )
    outcome2 = await judge_one(_FlakyLlm(["垃圾", good]), obj)
    assert outcome2.verdict == "FAIL"
    # LLM 故障两连 → ERROR(调用失败也重试一次)
    outcome3 = await judge_one(_FlakyLlm([]), obj)
    assert outcome3.verdict == "ERROR" and "评审失败" in (outcome3.error or "")


async def test_run_judge_single_object_failure_does_not_interrupt() -> None:
    """run 编排:单对象失败不中断(逐对象独立收口),汇总 ERROR 计分母。"""
    from evals.judge.client import JudgeLlmError
    from evals.judge.runner import run_judge
    from kongming.evalj.models import OBJECT_REPORT, JudgeObject

    class _HalfBrokenLlm:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
            self.calls += 1
            if "报告B" in user:
                raise JudgeLlmError("boom")
            return (
                '{"verdict":"PASS","dimensions":{"结构完整性":5,"数据准确性":4,'
                '"洞察价值":4,"表述质量":4},"quotes":["要点"]}'
            )

    class _MemStore:
        def __init__(self) -> None:
            self.finished: tuple[str, RunSummary, str] | None = None
            self.saved = 0

        async def create_run(
            self, *, object_type: str, rubric_version: str, prompt_fingerprint: str, note: str = ""
        ) -> str:
            return "run-1"

        async def save_result(self, run_id: str, outcome: JudgeOutcome, object_ref: str) -> None:
            self.saved += 1

        async def finish_run(self, run_id: str, summary: RunSummary, status: str) -> None:
            self.finished = (run_id, summary, status)

    objs = [
        JudgeObject(
            object_type=OBJECT_REPORT, object_id=f"r{i}", object_ref=f"报告{'AB'[i]}", text="t"
        )
        for i in range(2)
    ]
    store = _MemStore()
    _, outcomes, summary = await run_judge(_HalfBrokenLlm(), store, OBJECT_REPORT, objs)
    assert store.saved == 2  # 失败对象也落库(保留原始输出/错误)
    assert summary.total == 2 and summary.passed == 1 and summary.errored == 1
    assert summary.pass_rate == 0.5
    assert store.finished is not None and store.finished[2] == "COMPLETED"  # 单对象失败不砸 run
    assert outcomes[1].verdict == "ERROR"


def test_baseline_compare_is_pure_dataclass() -> None:
    c = BaselineCompare(
        comparable=False, red_line=False, note="n", previous_rate=None, current_rate=1.0
    )
    assert c.current_rate == 1.0


# ── 迁移结构断言(任务 1.1)+ 仓储回环(任务 1.2;测试库=alembic head)──


async def test_judge_tables_structure() -> None:
    from sqlalchemy import text

    from kongming.db import get_sessionmaker

    maker = get_sessionmaker()
    async with maker() as s:
        cols = {
            (r[0], r[1])
            for r in (
                await s.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns"
                        " WHERE table_name IN ('judge_run','judge_result')"
                    )
                )
            ).all()
        }
        assert ("judge_run", "object_type") in cols
        assert ("judge_run", "rubric_version") in cols
        assert ("judge_run", "prompt_fingerprint") in cols
        assert ("judge_run", "pass_rate") in cols
        assert ("judge_result", "run_id") in cols
        assert ("judge_result", "verdict") in cols
        assert ("judge_result", "dimensions") in cols
        assert ("judge_result", "quotes") in cols
        checks = {
            str(r[0])
            for r in (
                await s.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid IN"
                        " ('public.judge_run'::regclass,'public.judge_result'::regclass)"
                        " AND contype = 'c'"
                    )
                )
            ).all()
        }
        assert "judge_run_object_type_check" in checks
        assert "judge_run_status_check" in checks
        assert "judge_result_verdict_check" in checks


async def test_judge_repo_roundtrip_and_baseline_query() -> None:
    """开 run → 落结果(含 ERROR 原始输出)→ 收口;latest_completed 供基线对比。"""
    from sqlalchemy import text as _text

    from evals.judge.runner import run_judge
    from kongming.db import get_sessionmaker
    from kongming.evalj.models import OBJECT_REPORT, RUN_COMPLETED, RUN_FAILED, JudgeObject
    from kongming.evalj.repository import SqlJudgeRepo

    class _LlmPass:
        async def chat(self, system: str, user: str, *, max_tokens: int = 1200) -> str:
            return (
                '{"verdict":"PASS","dimensions":{"结构完整性":4,"数据准确性":4,'
                '"洞察价值":3,"表述质量":4},"quotes":["依据"]}'
            )

    objs = [
        JudgeObject(object_type=OBJECT_REPORT, object_id=f"rt-{i}", object_ref=f"报告{i}", text="t")
        for i in range(2)
    ]
    maker = get_sessionmaker()
    run_id = ""
    async with maker() as s:
        repo = SqlJudgeRepo(s)
        run_id, _outcomes, summary = await run_judge(
            _LlmPass(), repo, OBJECT_REPORT, objs, note="unit-roundtrip"
        )
        await s.commit()
        assert summary.total == 2 and summary.pass_rate == 1.0
        prev = await repo.latest_completed(object_type=OBJECT_REPORT, exclude_run_id=run_id)
        # 首轮或后续轮:排除自身后可能取到本测试早前残留(同值),只断言形态
        if prev is not None:
            assert prev.rubric_version and prev.status == "COMPLETED"
        results = await repo.list_results(run_id)
        assert len(results) == 2 and results[0].verdict == "PASS"
        assert results[0].dimensions["结构完整性"] == 4
    # 收口状态核验(表内直查)
    async with maker() as s:
        status = (
            await s.execute(
                _text("SELECT status FROM public.judge_run WHERE id = CAST(:i AS uuid)"),
                {"i": run_id},
            )
        ).scalar_one()
        assert status == RUN_COMPLETED
        assert status != RUN_FAILED


def test_judge_llm_missing_credential_explicit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """4.2 口径:judge 凭证缺失显式失败,不静默跳过(与 smoke LLM 凭证同口径)。"""
    from evals.judge.client import JudgeLlmError, llm_api_key

    monkeypatch.delenv("KM_EVAL_LLM_API_KEY", raising=False)
    monkeypatch.delenv("KM_AGENT_LLM_API_KEY", raising=False)
    with pytest.raises(JudgeLlmError, match="凭证缺失"):
        llm_api_key()
