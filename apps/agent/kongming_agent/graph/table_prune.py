"""按问题、术语、示例和指标的文本匹配分数筛选表结构，不增加外部调用。表名和问题命中的权重更高；没有命中时返回空集，关闭预筛时保留全部表。"""

import re
from dataclasses import dataclass, field

from kongming_agent.apiclient import MetaTable, SchemaMeta

# 权重(确定性常量,单一出处;调参须走 change,禁散落魔法数)
_W_NAME = 2  # 表名命中(表级信号)
_W_COL = 1  # 列名命中
_W_COMMENT = 1  # 表说明关键词命中
_W_QUESTION = 3  # 问句侧命中(意图直给)
_W_ASSET = 1  # 资产侧命中(术语/示例/指标,弱信号)

# 问句关键词切分:拉丁词(≥3 字符,弃 id/on 类噪音)+ CJK 2/3-gram
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class PruneHit:
    """单表命中记录(名次=列表序;sources 供 trace 可解释:哪类信号命中)。"""

    table: str  # schema.name
    score: int
    name: str
    sources: tuple[str, ...] = field(default_factory=tuple[str, ...])


def question_keywords(question: str) -> set[str]:
    """问句关键词集合(纯函数):拉丁词(len≥3)+ CJK 2/3-gram。"""
    out: set[str] = set()
    for word in _LATIN_RE.findall(question):
        if len(word) >= 3:
            out.add(word.lower())
    for run in _CJK_RUN_RE.findall(question):
        if len(run) < 2:
            continue
        for n in (2, 3):
            for i in range(len(run) - n + 1):
                out.add(run[i : i + n])
    return out


def _identity_tokens(table: MetaTable) -> list[str]:
    """表身份 token(纯函数):表名(整体 + snake 分段,≥2 字符)+ 列名(≥3 字符)。"""
    tokens: list[str] = [table.name.lower()]
    for part in re.split(r"[_\W]+", table.name.lower()):
        if len(part) >= 2:
            tokens.append(part)
    for col in table.columns:
        low = col.name.lower()
        if len(low) >= 3:
            tokens.append(low)
    return tokens


def _count(haystack: str, needle: str) -> int:
    """子串出现次数(haystack 已小写归一)。"""
    if not needle:
        return 0
    return haystack.count(needle)


def score_table(
    question: str,
    table: MetaTable,
    assets: str = "",
    keywords: set[str] | None = None,
) -> tuple[int, tuple[str, ...]]:
    """单表打分(纯函数):返回 (score, 命中来源)。零分=未命中。"""
    q = question.lower()
    a = assets.lower()
    kws = question_keywords(question) if keywords is None else keywords
    score = 0
    sources: list[str] = []
    name_low = table.name.lower()
    # 表名:问句直点(最高信号)/资产共现
    q_hits = sum(_count(q, kw) for kw in kws if kw in name_low)
    a_hits = _count(a, name_low) + sum(_count(a, part) for part in _identity_tokens(table))
    if q_hits:
        score += q_hits * _W_NAME * _W_QUESTION
        sources.append("question:name")
    if a_hits:
        # 资产侧封顶 1 次出现:共现是弱信号,防长术语段刷分压过问句直点
        # (问句表名命中 6 分 > 资产上限 2 分,「问句权重高于资产」结构性成立)
        score += min(a_hits, 1) * _W_NAME * _W_ASSET
        sources.append("asset:name")
    # 列名:问句/资产共现
    col_q = sum(
        _count(q, col.name.lower())
        for col in table.columns
        if len(col.name) >= 3 and col.name.lower() in q
    )
    col_a = sum(_count(a, col.name.lower()) for col in table.columns if len(col.name) >= 3)
    if col_q:
        score += col_q * _W_COL * _W_QUESTION
        sources.append("question:column")
    if col_a:
        score += min(col_a, 2) * _W_COL * _W_ASSET
        sources.append("asset:column")
    # 表说明:问句关键词落说明(主题相关)
    comment = (table.comment or "").lower()
    if comment:
        cmt_hits = sum(_count(comment, kw) for kw in kws if len(kw) >= 2)
        if cmt_hits:
            score += cmt_hits * _W_COMMENT * _W_QUESTION
            sources.append("question:comment")
    return score, tuple(sources)


def prune_tables(
    question: str,
    tables: list[MetaTable],
    assets: str = "",
    top_n: int = 12,
) -> list[PruneHit]:
    """表级预筛(纯函数):零命中返回空集;否则按分降序取前 top_n
    (同分按原表序,确定性)。**首位=最高相关命中**(预算首位豁免同源)。"""
    kws = question_keywords(question)
    hits: list[PruneHit] = []
    for t in tables:
        score, sources = score_table(question, t, assets=assets, keywords=kws)
        if score > 0:
            full = f"{t.schema}.{t.name}"
            hits.append(PruneHit(table=full, score=score, name=t.name, sources=sources))
    hits.sort(key=lambda h: -h.score)  # 同分保持原序(python sort 稳定)
    return hits[:top_n] if top_n > 0 else hits


def prune_schema(
    question: str,
    meta: SchemaMeta,
    assets: str = "",
    top_n: int = 12,
) -> tuple[SchemaMeta, list[PruneHit]]:
    """SchemaMeta → 预筛后 SchemaMeta(仅保留命中表,**按命中分降序**)+
    命中记录。零命中:tables 为空(调用方走既有哨兵形态)。truncated 原样透传。

    按分序(而非元数据原序)的三重一致性:trace 的 kept 列表即分序、预算
    的 schema 段 keep_head=1 首位豁免=最高相关命中(原序下首位是任意表,
    豁免保护错对象)、提示词首位=最相关表(注意力)。同分保持元数据原序
    (prune_tables 稳定排序),确定性不变。"""
    hits = prune_tables(question, meta.tables, assets=assets, top_n=top_n)
    by_name = {f"{t.schema}.{t.name}": t for t in meta.tables}
    kept = [by_name[h.table] for h in hits if h.table in by_name]
    return SchemaMeta(tables=kept, truncated=meta.truncated), hits
