"""NL2SQL 评估集测试:统计 Execution Match、有效执行率、表召回率和重试率,
覆盖 smoke、full、multiturn 三类用例。跨提示词指纹的运行不可直接比较。"""
# pyright: reportPrivateUsage=false

from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
from attribution import Attribution, classify_failure, summarize_attributions
from conftest import _load_yaml_cases, _tenant_of, call_chat, exec_sql, gen_tables
from matchers import result_set_equal, result_set_loose_match
from prompt_fingerprint import (
    fetch_prompt_fingerprint,
    format_cross_fingerprint_note,
    format_fingerprint_line,
    load_baseline_fingerprint,
    save_baseline_fingerprint,
)

FULL_PASS_FLOOR = 0.6  # 全量宽松及格线(上游 + 基线 76-78%,留采样波动余量)

# 会话级指纹(取一次,首行打印 + 四组分数同屏)
_current_fingerprint: str | None = None


class CaseFailure(AssertionError):
    """单题失败:携带该题的归因产物,full 统计块据它计分类计数。

    AssertionError 子类:smoke 端每题独立断言的既有语义不变。"""

    def __init__(self, msg: str, attribution: Attribution) -> None:
        super().__init__(msg)
        self.attribution = attribution


def _env_failure_attribution(reason: str) -> Attribution:
    """归类评估环境错误，例如 gold SQL 执行失败或 API 不可达。"""
    return Attribution(category="OTHER", reason=f"评估环境/资产错误(非链路质量失败): {reason}")


@pytest.fixture(scope="session", autouse=True)
def _prompt_fingerprint_report() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """评估运行首行打印 prompt 指纹;会话结束时跨指纹标注 + 记基线。"""
    global _current_fingerprint
    _current_fingerprint = fetch_prompt_fingerprint()
    print(format_fingerprint_line(_current_fingerprint), flush=True)
    yield
    note = format_cross_fingerprint_note(_current_fingerprint, load_baseline_fingerprint())
    if note:
        print(note, flush=True)
    save_baseline_fingerprint(_current_fingerprint)


async def _eval_single(case: dict[str, Any]) -> tuple[bool, bool, bool]:
    """单题评估：直接执行 gold SQL，与问答 API 返回结果比较，并计算表召回率。"""
    question = str(case["question"])
    tenant = _tenant_of(case)
    datasource_key = case.get("datasource_key")

    if case.get("expect_reject"):
        try:
            r = await call_chat(question, tenant, datasource_key=datasource_key)
        except httpx.HTTPError as e:
            # 传输层故障≠拒绝(httpx 对 4xx/5xx 响应不抛异常,真拒绝走下方
            # 服务未启动时不能把请求失败当成“拒绝”通过。
            pytest.fail(f"[{case['id']}] 链路不可达(评估环境未起?): {e}")
        rejected = r.status_code >= 400 or not r.json().get("success", False)
        if not rejected:
            try:
                body = cast("dict[str, Any]", r.json())
            except Exception:  # noqa: BLE001
                body = {}
            att = classify_failure(
                question=question,
                expect_reject=True,
                success=bool(body.get("success")),
                verdict=str(body.get("verdict", "")),
                gen_sql=str(body.get("sql", "")),
            )
            raise CaseFailure(
                f"\n[{case['id']}] 预期拒绝未生效:tenant={tenant} "
                f"用 datasource_key={datasource_key} 查询成功\n"
                f"  question: {question}\n  归因: [{att.category}] {att.reason}",
                att,
            )
        return False, True, True  # 拒绝用例不计 strict(防虚增),pass 由断言保证

    if case.get("expect_no_data"):
        # 负例:问题实体与可用表领域错配,预期哨兵短路 NO_DATA——
        # 业务终态(200+success=False+verdict=NO_DATA),非 expect_reject 的
        # HTTP 拒绝;SQL 为空=零执行硬断言
        r = await call_chat(question, tenant, datasource_key=datasource_key)
        r.raise_for_status()
        body = r.json()
        if body.get("success") or body.get("verdict") != "NO_DATA":
            att = classify_failure(
                question=question,
                expect_no_data=True,
                success=bool(body.get("success")),
                verdict=str(body.get("verdict", "")),
                gen_sql=str(body.get("sql", "")),
            )
            raise CaseFailure(
                f"\n[{case['id']}] 预期哨兵短路 NO_DATA,实际 success={body.get('success')} "
                f"verdict={body.get('verdict')}\n  question: {question}\n"
                f"  gen_sql: {body.get('sql')}\n  归因: [{att.category}] {att.reason}",
                att,
            )
        if body.get("sql"):
            att = classify_failure(
                question=question,
                expect_no_data=True,
                success=True,
                verdict=str(body.get("verdict", "")),
                gen_sql=str(body.get("sql", "")),
            )
            raise CaseFailure(
                f"[{case['id']}] NO_DATA 应零执行,却生成了 SQL: {body.get('sql')}\n"
                f"  归因: [{att.category}] {att.reason}",
                att,
            )
        return False, True, True  # 同拒绝用例口径:不计 strict,pass 由断言保证

    gold_sql = str(case["gold_sql"])
    expected_tables = set(case.get("expected_tables", []))
    gold_ordered = "order by" in gold_sql.lower()

    # gold 执行(失败=评估集本身有误,直接 fail);
    # gold_database=动态源题直连其独立业务库(与生成侧动态路由同落点)
    try:
        gold_cols, gold_rows = await exec_sql(gold_sql, database=case.get("gold_database"))
    except Exception as e:  # noqa: BLE001  # asyncpg 异常族统一收敛评估集错误
        pytest.fail(f"[{case['id']}] gold_sql 执行失败(评估集本身有误): {e}")

    # 以 persist=false 调用问答 API；服务不可达时直接失败。
    r = await call_chat(question, tenant, datasource_key=datasource_key)
    r.raise_for_status()
    body = r.json()
    gen_sql = str(body.get("sql", ""))
    gen_cols: list[str] = list(body.get("columns", []))
    gen_rows: list[list[Any]] = [list(row) for row in body.get("rows", [])]
    gen_valid = bool(body.get("success")) and bool(gen_sql)

    strict = gen_valid and result_set_equal(gold_rows, gen_rows)
    loose = gen_valid and result_set_loose_match(
        gold_cols, gold_rows, gen_cols, gen_rows, gold_ordered
    )
    recalled = bool(gen_sql) and (
        not expected_tables or expected_tables.issubset(gen_tables(gen_sql))
    )

    if not loose:
        # 确定性归因(纯函数;明细带分类名与判据原文,不只给结论)
        att = classify_failure(
            question=question,
            gold_sql=gold_sql,
            gen_sql=gen_sql,
            success=gen_valid,
            verdict=str(body.get("verdict", "")),
            expected_tables=expected_tables,
            gold_cols=gold_cols,
            gold_rows=gold_rows,
            gen_cols=gen_cols,
            gen_rows=gen_rows,
        )
        stage_tag = f"/{att.stage}" if att.stage else ""
        raise CaseFailure(
            f"\n[{case['id']}] Execution Match 失败(严格={strict})\n"
            f"  question: {question}\n"
            f"  gold_sql: {gold_sql}\n"
            f"  gen_sql:  {gen_sql}\n"
            f"  gold_rows({len(gold_rows)}): {gold_rows[:3]}\n"
            f"  gen_rows({len(gen_rows)}):  {gen_rows[:3]}\n"
            f"  gen_valid={gen_valid} retry={body.get('retry_count')} "
            f"tables_recalled={recalled}\n"
            f"  归因: [{att.category}{stage_tag}] {att.reason} | 信号: {att.signals}",
            att,
        )
    return strict, loose, recalled


# ============ smoke 集(评估环境显式触发,每题独立断言) ============


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """动态参数化:yaml 即资产,用例增删无需改测试代码(单轮/多轮分流)。"""
    cases = _load_yaml_cases()
    if "smoke_case" in metafunc.fixturenames:
        selected = [c for c in cases if c.get("smoke") and "turns" not in c]
        metafunc.parametrize("smoke_case", selected, ids=[str(c["id"]) for c in selected])
    if "multiturn_case" in metafunc.fixturenames:
        selected = [c for c in cases if "turns" in c]
        metafunc.parametrize("multiturn_case", selected, ids=[str(c["id"]) for c in selected])


@pytest.mark.smoke
async def test_smoke(smoke_case: dict[str, Any]) -> None:
    """smoke 集:每题独立断言宽松 Execution Match(防回归第一道闸)。"""
    await _eval_single(smoke_case)


# ============ full 集(全量单轮,统计+阈值) ============


@pytest.mark.full
async def test_full_all(all_cases: list[dict[str, Any]]) -> None:
    """全量单轮评估:宽松/严格双分统计,动态数据源组单列;阈值判宽松。

    strict pass ⇒ loose 必 pass(行精确相等则数值子集成立),宽松失败的题
    严格必失败,异常分支无需再细分。
    """
    single = [c for c in all_cases if "turns" not in c]
    # 计数器与失败明细分开放:混装 dict 的推断类型带 Unknown/list,算术自增全报错
    results: dict[str, int] = {
        "pass": 0,
        "strict_pass": 0,
        "total": 0,
        "recall_hit": 0,
        "ds_pass": 0,
        "ds_total": 0,
    }
    details: list[str] = []
    attributions: list[tuple[str, Attribution]] = []  # 失败归因(题目 id + 分类)
    for case in single:
        is_ds = case.get("datasource_key") or str(case.get("category", "")).startswith("datasource")
        try:
            strict, _loose, recalled = await _eval_single(case)
            results["pass"] += 1
            results["strict_pass"] += int(strict)
            results["recall_hit"] += int(recalled)
            if is_ds:
                results["ds_pass"] += 1
        except CaseFailure as e:
            # 失败时一并保存归因分类与判据原文。
            details.append(str(e))
            attributions.append((str(case["id"]), e.attribution))
        except pytest.fail.Exception as e:  # pytest.fail 抛 fail.Exception(即 Failed,
            # BaseException 子类),不进 Exception 网——不接住会中断全量统计
            # (gold 资产错误/deny 不可达);环境/资产错误显式落 OTHER 桶
            details.append(str(e))
            attributions.append((str(case["id"]), _env_failure_attribution(str(e)[:120])))
        except Exception as e:  # noqa: BLE001  # 单题异常不炸全量(计入失败)
            details.append(f"[{case['id']}] 异常: {e}")
            attributions.append((str(case["id"]), _env_failure_attribution(f"异常: {e}"[:120])))
        results["total"] += 1
        if is_ds:
            results["ds_total"] += 1

    total = results["total"]
    pass_rate = results["pass"] / total if total else 0.0
    strict_rate = results["strict_pass"] / total if total else 0.0
    recall_rate = results["recall_hit"] / total if total else 0.0
    fp_display = _current_fingerprint or "指纹不可得"
    print(
        f"\n=== 评估统计: 宽松 {results['pass']}/{total} ({pass_rate:.0%})"
        f" | 严格 {results['strict_pass']}/{total} ({strict_rate:.0%})"
        f" | 表命中 {results['recall_hit']}/{total} ({recall_rate:.0%})"
        f" | 动态数据源 {results['ds_pass']}/{results['ds_total']}"
        f" | prompt 指纹 {fp_display} ==="
    )
    # 跨指纹时显式标注运行不可比,防止静默套用基线阈值
    cross_note = format_cross_fingerprint_note(_current_fingerprint, load_baseline_fingerprint())
    if cross_note:
        print(cross_note)
    # 失败归因统计块:一级分类计数与明细
    if attributions:
        print(summarize_attributions(attributions))
    for d in details:
        print(d)
    assert pass_rate >= FULL_PASS_FLOOR, (
        f"评估宽松 pass 率 {pass_rate:.0%} 低于 {FULL_PASS_FLOOR:.0%} 阈值"
        f"(基线对照见 evals/README.md;劣化逐题溯源)"
    )


# ============ multiturn 集(多轮上下文) ============


@pytest.mark.multiturn
async def test_multiturn(multiturn_case: dict[str, Any]) -> None:
    """多轮上下文评估:同一 case 内顺序执行 turns,前序问句作 history。

    每轮独立断言 Execution Match(gold_sql 每轮独立可执行,不依赖上轮结果
    传参);history 由服务端截断最近 5 条,评估集 turns ≤5+1 轮内无感。
    """
    case_id = multiturn_case["id"]
    history: list[str] = []
    failures: list[str] = []
    for i, turn in enumerate(multiturn_case["turns"], 1):
        question = str(turn["question"])
        gold_sql = str(turn["gold_sql"])
        try:
            gold_cols, gold_rows = await exec_sql(gold_sql)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"[{case_id} turn{i}] gold_sql 执行失败(评估集有误): {e}")
        try:
            r = await call_chat(question, _tenant_of(multiturn_case), history=history or None)
            r.raise_for_status()
            body = r.json()
        except Exception as e:  # noqa: BLE001
            failures.append(f"turn{i} 链路调用失败: {e}")
            history.append(question)  # 仍累积,后续 turn 有上下文
            continue

        gen_sql = str(body.get("sql", ""))
        gen_cols: list[str] = list(body.get("columns", []))
        gen_rows: list[list[Any]] = [list(row) for row in body.get("rows", [])]
        gen_valid = bool(body.get("success")) and bool(gen_sql)
        strict = gen_valid and result_set_equal(gold_rows, gen_rows)
        loose = gen_valid and result_set_loose_match(
            gold_cols, gold_rows, gen_cols, gen_rows, "order by" in gold_sql.lower()
        )
        if not loose:
            # 失败轮次以同一纯函数归因(入参足够时;不做跨轮因果追责)
            att = classify_failure(
                question=question,
                gold_sql=gold_sql,
                gen_sql=gen_sql,
                success=gen_valid,
                verdict=str(body.get("verdict", "")),
                expected_tables=set(turn.get("expected_tables", [])),
                gold_cols=gold_cols,
                gold_rows=gold_rows,
                gen_cols=gen_cols,
                gen_rows=gen_rows,
            )
            stage_tag = f"/{att.stage}" if att.stage else ""
            failures.append(
                f"\n[{case_id} turn{i}] Execution Match 失败(严格={strict})\n"
                f"  question: {question}\n  history:  {history}\n"
                f"  gold_sql: {gold_sql}\n  gen_sql:  {gen_sql}\n"
                f"  gold_rows({len(gold_rows)}): {gold_rows[:3]}\n"
                f"  gen_rows({len(gen_rows)}):  {gen_rows[:3]}\n"
                f"  归因: [{att.category}{stage_tag}] {att.reason} | 信号: {att.signals}"
            )
        history.append(question)

    assert not failures, f"[{case_id}] 多轮评估失败:\n" + "\n".join(failures)
