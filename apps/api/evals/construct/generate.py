"""从知识面生成候选题并执行自洽验证。
(question/gold_sql/expected_tables/category),逐条执行自洽验证
(gold 直连执行 + 规范校验 + 语义去重),连同验证报告落候选集目录。
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

import yaml

from evals.construct.validators import validate_candidate
from evals.judge.client import JudgeLlm, JudgeLlmError

EVALS_DIR = Path(__file__).resolve().parent.parent
CANDIDATES_DIR = EVALS_DIR / "datasets" / "candidates"

CATEGORIES: tuple[str, ...] = (
    "single_table",
    "aggregate",
    "filter",
    "join_2table",
    "join_3table",
)
# 语义去重阈值(余弦缺省 0.92,超阈值标记重复)
DEDUP_COSINE_THRESHOLD = 0.92


@dataclass
class Candidate:
    """一条候选题 + 自洽验证报告(逐项校验结果)。id=题面稳定短哈希(并入定位)。"""

    question: str
    gold_sql: str
    expected_tables: list[str]
    category: str
    schema_name: str
    validation: dict[str, object] = field(default_factory=dict[str, object])

    @property
    def id(self) -> str:
        import hashlib

        return hashlib.sha256(self.question.encode("utf-8")).hexdigest()[:8]

    @property
    def passed(self) -> bool:
        return self.validation.get("ok") is True

    def to_yaml_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "question": self.question,
            "gold_sql": self.gold_sql,
            "expected_tables": self.expected_tables,
            "category": self.category,
            "validation": self.validation,
        }


class _Conn(Protocol):
    """asyncpg 连接最小端口(查询取行)。"""

    async def fetch(self, query: str, *args: object) -> Any: ...


async def read_schema_knowledge(conn: _Conn, tenant_id: str, datasource_key: str) -> str:
    """知识面装配(rag.table_descriptions 只读):表说明+字段语义+示例 SQL。"""
    rows = await conn.fetch(
        """
        SELECT schema_name, table_name, table_comment, columns_comment, sample_queries
        FROM rag.table_descriptions
        WHERE tenant_id = $1 AND datasource_key = $2
        ORDER BY schema_name, table_name
        """,
        tenant_id,
        datasource_key,
    )
    lines: list[str] = []
    for schema_name, table_name, table_comment, columns_comment, sample_queries in rows:
        header = f"表 {schema_name}.{table_name}"
        if table_comment:
            header += f":{table_comment}"
        lines.append(header)
        try:
            cols = cast("dict[str, str]", json.loads(str(columns_comment or "{}")))
        except json.JSONDecodeError:
            cols = {}
        for col, comment in cols.items():
            lines.append(f"  - {col}({comment})")
        try:
            samples = cast("list[object]", json.loads(str(sample_queries or "[]")))
        except json.JSONDecodeError:
            samples = []
        for sample in samples[:2]:
            lines.append(f"  示例: {sample}")
    return "\n".join(lines)


async def _existing_questions() -> list[str]:
    """既有评估集题面(语义去重基线;解析失败跳过该文件)。"""
    questions: list[str] = []
    for yml in sorted(EVALS_DIR.glob("*.yaml")):
        try:
            loaded = cast("object", yaml.safe_load(yml.read_text(encoding="utf-8")) or [])
        except yaml.YAMLError:
            continue
        if not isinstance(loaded, list):
            continue
        for case in loaded:
            if isinstance(case, dict) and case.get("question"):
                questions.append(str(case["question"]))
    return questions


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def dedup_flags(
    llm: JudgeLlm, candidates: list[Candidate], *, threshold: float = DEDUP_COSINE_THRESHOLD
) -> list[bool]:
    """语义去重(嵌入余弦):候选 vs 既有题面 + 候选两两;True=重复。
    embedding 失败按「无法判定」处理(False + 报告注明),不炸整批。"""
    questions = await _existing_questions()
    texts = questions + [c.question for c in candidates]
    try:
        vectors = await llm.embed(texts)
    except JudgeLlmError:
        return [False] * len(candidates)
    existing_vecs = vectors[: len(questions)]
    cand_vecs = vectors[len(questions) :]
    flags: list[bool] = []
    for i, cv in enumerate(cand_vecs):
        dup = any(_cosine(cv, ev) >= threshold for ev in existing_vecs)
        if not dup:
            dup = any(
                _cosine(cv, cand_vecs[j]) >= threshold for j in range(len(cand_vecs)) if j != i
            )
        flags.append(dup)
    return flags


_GEN_SYSTEM = (
    "你是评估题生成器。根据给定的数据库表知识,生成 NL2SQL 评估候选题。\n"
    "每题必须附带 gold_sql(在目标库可执行的标准答案 SQL)。要求:\n"
    f"1. category 只能取: {', '.join(CATEGORIES)}。\n"
    "2. gold_sql 全部表名显式 schema 限定(schema.table 形态);禁止 SELECT *;\n"
    "   投影业务可读列(名称列),不裸投影 ID;结果集必须非空(1..1000 行,\n"
    "   明细题加 LIMIT 100,聚合小集不加;零行题不可判定,生成即废)。\n"
    "3. ORDER BY ... LIMIT 形态必须追加唯一列(主键)作第二排序键。\n"
    "4. expected_tables 一律裸表名(不带 schema 前缀,与评估器表命中判据\n"
    "   同口径——schema 归 gold_sql 承载,两处形态不得混用)。\n"
    "5. 只输出一个 JSON 数组,不要输出任何其他内容:\n"
    '[{"question": "...", "gold_sql": "SELECT ...", "expected_tables": ["table"],\n'
    '  "category": "aggregate"}]'
)


async def generate_candidates(
    llm: JudgeLlm,
    schema_knowledge: str,
    *,
    per_category: int,
    report: dict[str, int] | None = None,
) -> list[Candidate]:
    """按 category 配额分批生成(design:分批防同质化)。

    LLM 失败/解析失败的批次数不静默丢弃:计入 report["failed_batches"]
    (调用方落报告/输出),否则某类别整批缺失只表现为候选数偏小、无失败痕迹。"""
    out: list[Candidate] = []
    failed_batches = 0
    for category in CATEGORIES:
        user = (
            f"表知识:\n{schema_knowledge}\n\n"
            f"本批只生成 category={category} 的题 {per_category} 条,题面互不重复。\n\n"
            "输出 JSON 数组:"
        )
        try:
            raw = await llm.chat(_GEN_SYSTEM, user, max_tokens=2400)
        except JudgeLlmError:
            failed_batches += 1
            continue
        parsed = parse_json_object_since_array(raw)
        if parsed is None:
            failed_batches += 1
            continue
        for item in parsed[:per_category]:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            gold_sql = str(item.get("gold_sql") or "").strip()
            if not question or not gold_sql:
                continue
            tables_raw = item.get("expected_tables")
            # 裸名归一(取末段):表命中判据=末段名子集比较,schema 前缀混入
            # 会让表命中恒 False(与 gold_sql 对错无关);schema 归 gold 承载
            tables = (
                [
                    str(t).rsplit(".", 1)[-1].strip()
                    for t in tables_raw
                    if isinstance(t, str) and str(t).strip()
                ]
                if isinstance(tables_raw, list)
                else []
            )
            cat = str(item.get("category") or category)
            schema_name = _schema_of(gold_sql, tables)
            out.append(
                Candidate(
                    question=question,
                    gold_sql=gold_sql,
                    expected_tables=tables,
                    category=cat if cat in CATEGORIES else category,
                    schema_name=schema_name,
                )
            )
    if report is not None:
        report["failed_batches"] = failed_batches
    return out


def parse_json_object_since_array(raw: str) -> list[object] | None:
    """容错解析 JSON 数组(剥标记/取首尾方括号);不可解析返回 None。"""
    text = raw.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, list) else None


def _schema_of(gold_sql: str, tables: list[str]) -> str:
    for t in tables:
        if "." in t:
            return t.split(".")[0]
    m = re.search(r"\b(?:from|join)\s+([a-zA-Z_]\w*)\.", gold_sql, re.IGNORECASE)
    return m.group(1) if m else "public"


async def self_check(
    conn: _Conn,
    llm: JudgeLlm,
    candidates: list[Candidate],
    *,
    schema_name: str,
) -> None:
    """自洽验证:gold 直连执行 → 规范校验 → 语义去重,逐项落报告。
    单条候选执行失败=该候选验证 FAIL(带原因),不中断整批。"""
    flags = await dedup_flags(llm, candidates)
    for cand, dup in zip(candidates, flags, strict=True):
        report: dict[str, object] = {}
        row_count: int | None = None
        try:
            rows = await conn.fetch(cand.gold_sql)
            row_count = len(rows)
            report["exec_ok"] = True
            report["row_count"] = row_count
        except Exception as e:  # noqa: BLE001  asyncpg 异常族统一为校验失败
            report["exec_ok"] = False
            report["exec_error"] = str(e)[:300]
        style_failures = validate_candidate(
            sql=cand.gold_sql,
            schema=cand.schema_name or schema_name,
            row_count=row_count,
        )
        report["style_failures"] = style_failures
        report["semantic_duplicate"] = dup
        report["ok"] = (
            bool(report.get("exec_ok")) and not style_failures and not dup and row_count is not None
        )
        cand.validation = report


def write_candidates(tenant: str, candidates: list[Candidate]) -> Path:
    """候选+验证报告落目录:datasets/candidates/<tenant>/<批次>.yaml。"""
    target_dir = CANDIDATES_DIR / tenant
    target_dir.mkdir(parents=True, exist_ok=True)
    batch = f"{max((p.stat().st_mtime for p in target_dir.glob('*.yaml')), default=0.0):.0f}-auto"
    path = target_dir / f"{batch}.yaml"
    payload = [c.to_yaml_dict() for c in candidates]
    path.write_text(
        yaml.dump(payload, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8"
    )
    return path
