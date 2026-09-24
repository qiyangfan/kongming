"""Skill Pack 领域纯函数(056):四段契约校验与合并投影封顶。

零框架依赖(宪法 Article II)。fewshot SQL 只作提示示例文本,永不执行。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

GLOSSARY_CAP = 50
FEWSHOTS_CAP = 10
PERSONA_CAP = 800

FEWSHOT_BAN_PREFIX = "以下为问法风格示例,表名列名不可照抄:"

PACK_STATUS = ("ACTIVE", "RETIRED")


@dataclass
class GlossaryItem:
    term: str
    aliases: list[str] = field(default_factory=list[str])
    definition: str = ""
    hint: str = ""


@dataclass
class Fewshot:
    question: str
    sql: str


@dataclass
class MetricSpec:
    metric_name: str
    sql_template: str
    business_terms: list[str] = field(default_factory=list[str])


@dataclass
class SkillPackContent:
    glossary: list[GlossaryItem] = field(default_factory=list[GlossaryItem])
    fewshots: list[Fewshot] = field(default_factory=list[Fewshot])
    persona: str = ""
    metrics: list[MetricSpec] = field(default_factory=list[MetricSpec])


def validate_manifest(pack_key: str, name: str, version: str) -> str | None:
    if not pack_key or not pack_key.replace("_", "").replace("-", "").isalnum():
        return "pack_key 必须为字母数字/_/-"
    if not name.strip():
        return "name 必填"
    if not version.strip():
        return "version 必填"
    return None


def _as_json_mapping(val: object) -> dict[str, object]:
    """json/jsonb 经 asyncpg 常返回 str(与 system.parse_json_agg 同口径)。"""
    if isinstance(val, Mapping):
        raw = cast("Mapping[object, object]", val)
        return {str(k): v for k, v in raw.items()}
    if isinstance(val, (str, bytes)):
        try:
            obj = json.loads(val)
        except (TypeError, ValueError):
            return {}
        if isinstance(obj, Mapping):
            raw = cast("Mapping[object, object]", obj)
            return {str(k): v for k, v in raw.items()}
        return {}
    return {}


def _field_str(m: Mapping[str, object], key: str, *, cap: int = 0) -> str:
    s = str(m.get(key) or "").strip()
    return s[:cap] if cap else s


def parse_content(raw: object) -> SkillPackContent:
    """解析四段(可选 metrics);未知键忽略;结构非法项丢弃。"""
    data = _as_json_mapping(raw)
    glossary: list[GlossaryItem] = []
    for item in _as_list(data.get("glossary")):
        if not isinstance(item, Mapping):
            continue
        m = cast("Mapping[str, object]", item)
        term = _field_str(m, "term")
        if not term:
            continue
        aliases = [str(a).strip() for a in _as_list(m.get("aliases")) if str(a).strip()]
        glossary.append(
            GlossaryItem(
                term=term,
                aliases=aliases[:10],
                definition=_field_str(m, "definition", cap=500),
                hint=_field_str(m, "hint", cap=500),
            )
        )
    fewshots: list[Fewshot] = []
    for item in _as_list(data.get("fewshots")):
        if not isinstance(item, Mapping):
            continue
        m = cast("Mapping[str, object]", item)
        q = _field_str(m, "question")
        sql = _field_str(m, "sql")
        if q and sql:
            fewshots.append(Fewshot(question=q[:500], sql=sql[:2000]))
    persona = _field_str(data, "persona", cap=2000)
    metrics: list[MetricSpec] = []
    for item in _as_list(data.get("metrics")):
        if not isinstance(item, Mapping):
            continue
        m = cast("Mapping[str, object]", item)
        n = _field_str(m, "metric_name")
        tpl = _field_str(m, "sql_template")
        if n and tpl:
            metrics.append(
                MetricSpec(
                    metric_name=n[:128],
                    sql_template=tpl[:2000],
                    business_terms=[
                        str(t).strip() for t in _as_list(m.get("business_terms")) if str(t).strip()
                    ][:20],
                )
            )
    return SkillPackContent(glossary=glossary, fewshots=fewshots, persona=persona, metrics=metrics)


def _as_list(val: object) -> list[object]:
    if isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
        return list(cast("Sequence[object]", val))
    return []


def merge_packs(packs: Sequence[SkillPackContent]) -> SkillPackContent:
    """多包简单拼接后截断封顶(冲突消解不承诺)。"""
    glossary: list[GlossaryItem] = []
    fewshots: list[Fewshot] = []
    persona_parts: list[str] = []
    metrics: list[MetricSpec] = []
    for p in packs:
        glossary.extend(p.glossary)
        fewshots.extend(p.fewshots)
        if p.persona:
            persona_parts.append(p.persona)
        metrics.extend(p.metrics)
    persona = "\n".join(persona_parts)[:PERSONA_CAP]
    return SkillPackContent(
        glossary=glossary[:GLOSSARY_CAP],
        fewshots=fewshots[:FEWSHOTS_CAP],
        persona=persona,
        metrics=metrics,
    )


def project_terms(content: SkillPackContent) -> str:
    """术语段;零条返回空串(零回归)。"""
    lines: list[str] = []
    for g in content.glossary:
        alias = f"(又称 {', '.join(g.aliases)})" if g.aliases else ""
        hint = f";口径提示:{g.hint}" if g.hint else ""
        lines.append(f"- {g.term}{alias}:{g.definition}{hint}")
    return "\n".join(lines)


def project_examples(content: SkillPackContent) -> str:
    """黄金示例段;带禁令前缀。fewshot SQL 仅文本,不执行。"""
    if not content.fewshots:
        return ""
    lines = [FEWSHOT_BAN_PREFIX]
    for i, f in enumerate(content.fewshots, start=1):
        lines.append(f"{i}. 问:{f.question}")
        lines.append(f"   答 SQL 形态:{f.sql}")
    return "\n".join(lines)


def project_persona(content: SkillPackContent) -> str:
    return content.persona


@dataclass
class SkillProjection:
    skill_terms: str = ""
    skill_examples: str = ""
    skill_persona: str = ""


def build_projection(packs: Sequence[SkillPackContent]) -> SkillProjection:
    """合并投影三段;全空=零注入(生成链路零回归)。"""
    merged = merge_packs(packs)
    return SkillProjection(
        skill_terms=project_terms(merged),
        skill_examples=project_examples(merged),
        skill_persona=project_persona(merged),
    )


def metric_name_conflicts(
    incoming: Sequence[MetricSpec],
    existing: Sequence[Mapping[str, object]] | Sequence[str],
) -> list[str]:
    """metric_name 冲突判定:启用前检查。

    同名且定义(sql_template/business_terms)一致 = 幂等 upsert,放行——
    否则重启用/禁用后重启用会与自己先前登记的指标冲突。仅知名列表(无定义)
    保持保守:同名即冲突。冲突则拒绝启用。
    """
    exist: dict[str, Mapping[str, object] | None] = {}
    for r in existing:
        if isinstance(r, str):
            exist[r.lower()] = None
        else:
            name = str(r.get("metric_name") or "").lower()
            if name:
                exist[name] = r
    out: list[str] = []
    for m in incoming:
        key = m.metric_name.lower()
        if key not in exist:
            continue
        hit = exist[key]
        if hit is None:
            out.append(m.metric_name)
            continue
        same_tpl = str(hit.get("sql_template") or "") == m.sql_template
        same_terms = {str(t) for t in _as_list(hit.get("business_terms"))} == set(m.business_terms)
        if not (same_tpl and same_terms):
            out.append(m.metric_name)
    return out
