"""答案评分确定性公式与分解净化(纯函数,零框架依赖,宪法 Article II)。

职责分工(spec「答案异步评分」):评审模型只产出结构化主张分解,分数由
本模块按公式重算——同分解必同分;评审输出中的任何分数字段不建模、
不透传。公式权重、封顶、净化规则与低分阈值单源收口在本模块,调参只改
这一处;评审侧 prompt 的口径(主张定义/三档重要度/三级支撑)须与本表
保持一致,两侧同源演化。
"""

from dataclasses import dataclass
from typing import cast

# 主张重要度三档(权重)与支撑三级(分值);未知取值防御性归 normal/unsupported
IMPORTANCE_WEIGHTS: dict[str, float] = {"critical": 1.0, "normal": 0.6, "minor": 0.3}
SUPPORT_SCORES: dict[str, float] = {
    "fully_supported": 1.0,
    "partially_supported": 0.5,
    "unsupported": 0.0,
}

GROUNDING_WEIGHT = 0.6
COVERAGE_WEIGHT = 0.4
# critical 主张无支撑=硬伤:总分封顶(防其余主张支撑良好拉高无依据轮)
CRITICAL_UNSUPPORTED_CAP = 40
# 质量台低分轮计数阈值(汇总观测口径,与呈现层共享)
LOW_SCORE_THRESHOLD = 60
# 分解主张数上限(prompt 侧同限,此处防御性截断防超长分解拖垮公式与存储)
MAX_CLAIMS = 12

# 证据截断预算(design D3):行级证据防 prompt 爆炸
EVIDENCE_MAX_ROWS = 50
EVIDENCE_MAX_CHARS = 8_000
DOC_EVIDENCE_PER_SOURCE_CHARS = 500
DOC_EVIDENCE_MAX_SOURCES = 8

_IMPORTANCE_DEFAULT = "normal"
_SUPPORT_DEFAULT = "unsupported"


@dataclass(frozen=True)
class Claim:
    """单条事实主张:judge 原文 + 分级;excerpt 为证据摘录(可空)。"""

    text: str
    importance: str
    support: str
    excerpt: str = ""


@dataclass(frozen=True)
class ScoreDecomposition:
    """评审分解收口:净化前的完整主张 + 子问题要点覆盖计数。

    points_total=0 表示评审未产出要点拆解(coverage 回退依据分,见
    compute_score 单要点/无要点口径)。"""

    claims: tuple[Claim, ...]
    points_total: int
    points_covered: int


@dataclass(frozen=True)
class TurnScore:
    """确定性重算产物:三分均落库(int,0-100)。"""

    score: int
    grounding: int
    coverage: int


def parse_decomposition(raw: object) -> ScoreDecomposition | None:
    """评审输出 JSON → 分解(防御性解析,纯函数)。

    主张缺文本/非字符串丢弃;importance/support 未知取值归
    normal/unsupported(宁低勿高);主张数超上限截断;claims 为空或
    非法 → None(该轮不评分,与「无主张分解不评分」守卫同义)。"""
    if not isinstance(raw, dict):
        return None
    body = cast("dict[str, object]", raw)
    raw_claims = body.get("claims")
    claims: list[Claim] = []
    if isinstance(raw_claims, list):
        for entry in cast("list[object]", raw_claims[:MAX_CLAIMS]):
            if not isinstance(entry, dict):
                continue
            item = cast("dict[str, object]", entry)
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            importance = item.get("importance")
            support = item.get("support")
            excerpt = item.get("excerpt")
            imp = (
                importance
                if isinstance(importance, str) and importance in IMPORTANCE_WEIGHTS
                else _IMPORTANCE_DEFAULT
            )
            sup = (
                support
                if isinstance(support, str) and support in SUPPORT_SCORES
                else _SUPPORT_DEFAULT
            )
            claims.append(
                Claim(
                    text=text.strip(),
                    importance=imp,
                    support=sup,
                    excerpt=excerpt.strip() if isinstance(excerpt, str) else "",
                )
            )
    if not claims:
        return None
    points_total, points_covered = _coverage_of(cast("dict[str, object]", raw))
    return ScoreDecomposition(
        claims=tuple(claims), points_total=points_total, points_covered=points_covered
    )


def _coverage_of(raw: dict[str, object]) -> tuple[int, int]:
    """子问题要点覆盖计数(防御:负数/越界钳到合法区间)。"""
    sq = raw.get("sub_questions")
    if not isinstance(sq, list):
        return 0, 0
    total = 0
    covered = 0
    for item in cast("list[object]", sq):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        total += 1
        if entry.get("answered") is True:
            covered += 1
    covered = max(0, min(covered, total))
    return total, covered


def purify_claims(claims: tuple[Claim, ...]) -> tuple[Claim, ...]:
    """主张净化(公式入口统一):滤除非事实性主张——minor 重要度 + 无支撑
    + 摘录与主张均不含可验证实体(数字)的语句(主观评价/润色收尾句)
    不参与依据分,防「回答很有礼貌」抬高分数;critical/normal 主张与
    任何含数字的主张一律保留(宁罚勿纵)。"""
    kept: list[Claim] = []
    for c in claims:
        no_entity = not _has_digit(c.text + c.excerpt)
        if c.importance == "minor" and c.support == "unsupported" and no_entity:
            continue
        kept.append(c)
    return tuple(kept)


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def compute_score(d: ScoreDecomposition) -> TurnScore:
    """分解 → 三分(确定性;净化后重算,critical 无支撑硬伤封顶)。

    依据分 G=Σ(重要度权重×支撑分)/Σ(重要度权重);覆盖分 C=已覆盖要点
    占比(要点拆解为 0 时 C 取 G——单事实/未拆解轮不因粒度吃亏);
    总分=100×(0.6G+0.4C)² 平方校准(抑制分解普遍偏宽的虚高)。"""
    purified = purify_claims(d.claims)
    if not purified:
        # 净化后无剩余主张:全部为非事实句——依据分按 0 收口(封顶兜底)
        g = 0.0
    else:
        weight_sum = sum(IMPORTANCE_WEIGHTS.get(c.importance, 0.0) for c in purified)
        g = (
            sum(
                IMPORTANCE_WEIGHTS.get(c.importance, 0.0) * SUPPORT_SCORES.get(c.support, 0.0)
                for c in purified
            )
            / weight_sum
            if weight_sum > 0
            else 0.0
        )
    c = d.points_covered / d.points_total if d.points_total > 0 else g
    raw = GROUNDING_WEIGHT * g + COVERAGE_WEIGHT * c
    score = round(100 * raw * raw)
    if any(c.importance == "critical" and c.support == "unsupported" for c in purified):
        score = min(score, CRITICAL_UNSUPPORTED_CAP)
    score = max(0, min(score, 100))
    return TurnScore(score=score, grounding=round(100 * g), coverage=round(100 * min(c, 1.0)))


def decomposition_payload(d: ScoreDecomposition) -> dict[str, object]:
    """分解 → 落库 JSON(审计形态):purified=参与依据分的主张;
    excluded=净化剔除的非事实主张(审计可见剔除依据,非黑箱)。"""
    purified = purify_claims(d.claims)
    kept_ids = {id(c) for c in purified}
    excluded = [c for c in d.claims if id(c) not in kept_ids]

    def _dump(c: Claim) -> dict[str, str]:
        return {
            "text": c.text,
            "importance": c.importance,
            "support": c.support,
            "excerpt": c.excerpt,
        }

    return {
        "claims": [_dump(c) for c in purified],
        "excluded_claims": [_dump(c) for c in excluded],
        "points_total": d.points_total,
        "points_covered": d.points_covered,
    }


def assemble_data_evidence(
    columns: list[str],
    rows: list[list[object]],
    stats: dict[str, object],
    *,
    max_rows: int = EVIDENCE_MAX_ROWS,
    max_chars: int = EVIDENCE_MAX_CHARS,
) -> str:
    """数据轮证据装配(纯函数):结果行列 + 统计 → 评审可读文本
    (行数截断 + 总长截断,超限附标记)。"""
    lines: list[str] = [f"列: {', '.join(columns)}" if columns else ""]
    shown = rows[:max_rows]
    lines.extend(" | ".join(_cell(v) for v in r) for r in shown)
    if len(rows) > max_rows:
        lines.append(f"(仅展示前 {max_rows} 行,共 {len(rows)} 行)")
    if stats:
        stat_items = [f"{k}={_cell(v)}" for k, v in list(stats.items())[:20]]
        lines.append("统计: " + "; ".join(stat_items))
    text = "\n".join(ln for ln in lines if ln)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n(证据超长已截断)"
    return text


def assemble_doc_evidence(
    sources: list[dict[str, str]],
    contents: dict[str, str],
    *,
    per_source_max: int = DOC_EVIDENCE_PER_SOURCE_CHARS,
    max_sources: int = DOC_EVIDENCE_MAX_SOURCES,
) -> str:
    """文档/融合轮证据装配(纯函数):引用来源对应文档摘录(每来源截断
    + 来源数截断)。contents 键=source_ref;缺内容的来源跳过。"""
    parts: list[str] = []
    for s in sources[:max_sources]:
        ref = s.get("source_ref") or ""
        content = contents.get(ref)
        if not content:
            continue
        name = s.get("doc_name") or ref
        excerpt = content.strip()[:per_source_max]
        parts.append(f"[{name}]\n{excerpt}")
    if not parts:
        return ""
    return "\n\n".join(parts)


def build_material(
    *,
    question: str,
    resolved_question: str | None,
    answer: str,
    evidence: str,
) -> str:
    """评审材料装配(纯函数):问句(优先改写后自包含问句)+ 答案 + 证据。"""
    q = (resolved_question or question or "").strip()
    parts = [f"用户问题: {q}", f"助手回答:\n{answer.strip()}"]
    if evidence.strip():
        parts.append(f"对照证据:\n{evidence.strip()}")
    return "\n\n".join(parts)


def _cell(v: object) -> str:
    """单元格 → 评审可读文本(None→空,浮点收两位)。"""
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)
