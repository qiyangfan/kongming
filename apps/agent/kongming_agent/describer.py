"""表元数据 LLM 润色(/internal/describe-table 核心逻辑,009)。

prompt 资产纪律:SYSTEM/USER 模板为上游 table_describer.py 移植(全角标点!),
改动前先比对上游原文,手抄易丢全角。例外(037 增量,不再与上游逐字一致):
_USER_PROMPT_TEMPLATE 新增第 4 条 alias 产出要求(业务称谓别名,同一次调用
附带,零增量成本;alias 缺失/畸形按空降级不判润色失败)。其余部分仍逐字保值。
输入:表结构 JSON(表名/schema/列名/类型/注释——introspection 产物,非业务行数据)。
输出:中文表说明 + 列说明(保留类型前缀)+ 2-3 条示例 SQL + 业务称谓别名(037)。
安全边界(ADR-002):出域给 LLM 的只有表结构元数据,不查行业务行数据。
降级契约:LLM 失败/超时/输出非法 → 抛异常,由 api 侧降级为纯结构内容(不阻断)。
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import cast

from pydantic import BaseModel, Field, field_validator

from kongming_agent.llm import LlmClient, LlmError

logger = logging.getLogger(__name__)

# 输出上限(防御 LLM 产出过长内容撑爆 table_descriptions 文本列)
_MAX_COMMENT_LEN = 200
_MAX_COL_DESC_LEN = 200
_MAX_SQL_LEN = 500
_MAX_SAMPLES = 3
_MAX_ALIAS_LEN = 64  # 037:alias 上限(对齐 VARCHAR(64))
# 结构性字符(换行/括号/竖线)破坏 api 侧 schema_hint 行形态——按空降级(R1-F1)
_ALIAS_STRUCT_RE = re.compile(r"[\r\n()（）|]")
# 列名标识符(与 api 侧 parser columnsToJson 同口径)
_IDENT = re.compile(r"^[a-zA-Z_][\w]*$")

_SYSTEM_PROMPT = (
    "你是数据库文档专家。根据给定的 PostgreSQL 表结构，"
    "生成中文表说明、列说明、示例 SQL 和业务称谓别名。只输出 JSON，不要输出任何其他文字。"
)

_USER_PROMPT_TEMPLATE = """表：{schema_name}.{table_name}
表注释：{table_comment}
列：
{columns_text}

要求：
1. table_comment：一句话中文表说明（≤50字），根据表名/列名推测业务含义
2. columns_comment：对象，键为列名，值为 "类型 中文说明"（必须保留原始类型前缀，覆盖上面所有列）
3. sample_queries：2-3 条示例查询 SQL，全部是只读 SELECT，表名必须带 schema 前缀
   （如 {schema_name}.{table_name}），其中至少一条是 GROUP BY 聚合统计
4. alias：业务称谓别名（如「商品表」「影片分类表」），≤10 字的中文短名，
   禁止用「数据表」「信息表」等泛泛名称，禁止出现「表」字堆砌
   （用户若已知该表的常用称谓请直接使用；若无法推断则 alias 为空字符串）

严格输出如下 JSON（不要输出 markdown 代码块标记）：
{{"table_comment": "...", "columns_comment": {{"列名": "类型 中文说明"}}, "sample_queries": ["SELECT ..."], "alias": "业务称谓"}}"""  # noqa: E501  # JSON 输出模板单行示例,拆行会改变给 LLM 的格式契约(上游同款)


class DescribedTable(BaseModel):
    """LLM 润色结果(Pydantic 校验,输出契约)。037:新增 alias 业务称谓别名。"""

    table_comment: str = Field(default="")
    columns_comment: dict[str, str] = Field(default_factory=dict)
    sample_queries: list[str] = Field(default_factory=list)
    alias: str = Field(default="")  # 037:业务称谓别名

    @field_validator("table_comment")
    @classmethod
    def _cap_comment(cls, v: str) -> str:
        return (v or "").strip()[:_MAX_COMMENT_LEN]

    @field_validator("alias")
    @classmethod
    def _cap_alias(cls, v: str) -> str:
        # alias 缺失/畸形按空字符串降级(D2:不判润色失败);换行/括号等结构性
        # 字符会破坏 api 侧 schema_hint 行形态(R1-F1)——同样按空降级
        folded = " ".join((v or "").split())[:_MAX_ALIAS_LEN]
        return "" if _ALIAS_STRUCT_RE.search(folded) else folded


class DescribeError(Exception):
    """润色失败(输入非法/LLM 异常/输出不可解析)——api 侧按失败降级纯结构。"""


@dataclass(frozen=True)
class ColumnSpec:
    """列结构(introspection 产物形态)。"""

    name: str
    type: str
    comment: str = ""


@dataclass(frozen=True)
class TableSpec:
    """表结构(introspection 产物形态,非业务行数据)。"""

    table_name: str
    schema_name: str
    table_comment: str = ""
    columns: list[ColumnSpec] = field(default_factory=list[ColumnSpec])


async def describe_table(spec: TableSpec, llm: LlmClient) -> DescribedTable:
    """调 LLM 生成中文表说明。任何失败抛 DescribeError(api 侧 catch 降级纯结构)。

    返回的 columns_comment 只保留真实存在的列(防 LLM 臆造列名污染知识库)。
    """
    if not spec.table_name or not _IDENT.match(spec.table_name):
        raise DescribeError(f"非法表名: {spec.table_name!r}")
    if not spec.columns:
        raise DescribeError("列清单为空,无法润色")

    real_cols = {c.name for c in spec.columns if c.name}
    columns_text = "\n".join(
        f"- {c.name} ({c.type or 'unknown'})" + (f" -- {c.comment}" if c.comment else "")
        for c in spec.columns
    )
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        schema_name=spec.schema_name,
        table_name=spec.table_name,
        table_comment=spec.table_comment or "（无）",
        columns_text=columns_text,
    )

    try:
        raw = await llm.chat(
            _SYSTEM_PROMPT, user_prompt, temperature=0.1, task="DESCRIBE"
        )
    except LlmError as e:
        raise DescribeError(f"LLM 调用失败: {e}") from e
    data = _parse_json_loose(raw)

    try:
        result = DescribedTable.model_validate(data)
    except Exception as e:
        raise DescribeError(f"LLM 输出不可解析: {e}") from e

    # 过滤臆造列(只保留真实列);值截断防超长
    result.columns_comment = {
        k: (v or "").strip()[:_MAX_COL_DESC_LEN]
        for k, v in result.columns_comment.items()
        if k in real_cols
    }
    # 示例 SQL 只保留只读 SELECT(防 LLM 产出 DML 文本落库;示例仅作 few-shot 文本不执行)
    # INTO 判定用 \bINTO\b 词边界正则——字面 " INTO " 可被 \n/\t 与 SQL 注释绕过;
    # 多语句=去尾分号后仍含分号。条数 ≤3。
    cleaned_samples: list[str] = []
    for sql in result.sample_queries:
        s = sql.strip()
        upper = " ".join(s.upper().split())
        multi_stmt = ";" in upper.rstrip(";")
        if (
            upper.startswith("SELECT")
            and not re.search(r"\bINTO\b", upper)
            and not multi_stmt
            and len(s) <= _MAX_SQL_LEN
        ):
            cleaned_samples.append(s)
        if len(cleaned_samples) >= _MAX_SAMPLES:
            break
    result.sample_queries = cleaned_samples

    if not result.table_comment and not result.columns_comment:
        raise DescribeError("LLM 输出为空(无表说明且无列说明),按失败降级")

    logger.info(
        "表元数据润色完成: %s.%s, 列说明 %d 条, 示例 %d 条",
        spec.schema_name,
        spec.table_name,
        len(result.columns_comment),
        len(result.sample_queries),
    )
    return result


def _parse_json_loose(raw: str) -> dict[str, object]:
    """LLM 返回 JSON 的容错解析:剥 markdown 标记、取首尾花括号子串。"""
    text = raw.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError as e:
                raise DescribeError(f"LLM 输出非合法 JSON: {e}") from e
        else:
            raise DescribeError("LLM 输出非 JSON") from None
    if not isinstance(parsed, dict):
        raise DescribeError("LLM 输出非 JSON 对象")
    # isinstance 收窄产物 dict[Unknown, Unknown] 仍报,守卫后收窄键型
    return cast("dict[str, object]", parsed)


def fallback_description(spec: TableSpec) -> DescribedTable:
    """降级产物:纯结构内容(英文类型列说明,无示例)——api 侧落库时标记 INTROSPECTION。"""
    return DescribedTable(
        table_comment=spec.table_comment or "",
        columns_comment={c.name: c.type for c in spec.columns},
        sample_queries=[],
        alias="",  # 037:降级无 alias
    )


__all__ = [
    "ColumnSpec",
    "DescribeError",
    "DescribedTable",
    "TableSpec",
    "describe_table",
    "fallback_description",
]
