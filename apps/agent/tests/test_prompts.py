"""prompt 资产护栏单测(018):新规则关键标识在位,防后续手滑删规则/丢全角。

标识清单与 design D2/D3/D4 逐字对拍;全角纪律只查「汉字,汉字」形态
(SQL 函数参数如 ROUND(表达式, 2) 的半角逗号为语法豁免)。
"""

import re

from kongming_agent.graph import prompts


def test_sql_gen_write_request_honest_select() -> None:
    assert (
        "用户请求写操作(增/删/改)时，也只生成查看相关数据现状的只读 SELECT"
        in prompts.SQL_GEN_SYSTEM
    )
    assert "❌ 用 WHERE FALSE 等形态假装完成写操作" in prompts.SQL_GEN_SYSTEM


def test_sql_gen_stats_no_limit_boundary() -> None:
    assert (
        "含 GROUP BY 或聚合函数(COUNT/AVG/SUM/MAX/MIN) "
        "的查询一律视为统计查询，不加 LIMIT" in prompts.SQL_GEN_SYSTEM
    )
    assert '用户明确要求"前 N 名/top N"时除外' in prompts.SQL_GEN_SYSTEM
    assert "❌ 统计查询(GROUP BY/聚合)加 LIMIT 100" in prompts.SQL_GEN_SYSTEM


def test_sql_gen_tiebreaker_rule() -> None:
    assert "凡 ORDER BY … LIMIT 取前 N 的查询（明细或统计 top-N)" in prompts.SQL_GEN_SYSTEM
    assert "排序键可能并列时必须追加主键等唯一列作第二排序键" in prompts.SQL_GEN_SYSTEM
    assert "❌ 排序键可能并列却不追加唯一列" in prompts.SQL_GEN_SYSTEM


def test_sql_gen_round_convention() -> None:
    assert "ROUND(表达式, 2) 保留两位小数" in prompts.SQL_GEN_SYSTEM
    assert "禁止用 ::numeric 转换代替 ROUND 或全精度直出" in prompts.SQL_GEN_SYSTEM
    assert "❌ 平均值全精度直出或用 ::numeric 转换代替 ROUND" in prompts.SQL_GEN_SYSTEM


def test_sql_gen_projection_and_distinct() -> None:
    assert "问题点名的每个信息项（名称/数量/金额等）都必须出现在 SELECT 投影中" in (
        prompts.SQL_GEN_SYSTEM
    )
    assert "COUNT(DISTINCT 实体主键)" in prompts.SQL_GEN_SYSTEM
    assert "JOIN 产生完全重复的明细行时用 DISTINCT 去重" in prompts.SQL_GEN_SYSTEM
    assert "❌ 问题点名的信息项漏出投影" in prompts.SQL_GEN_SYSTEM


def test_sql_gen_id_not_name_substitute() -> None:
    assert "ID/编号列不能替代名称列" in prompts.SQL_GEN_SYSTEM
    assert "❌ 用 ID/编号列替代名称列" in prompts.SQL_GEN_SYSTEM


def test_understand_fidelity_rules() -> None:
    assert "补全必须保留上一轮的全部限定条件（筛选/排序/数量限制/聚合口径）" in (
        prompts.UNDERSTAND_MULTI_SYSTEM
    )
    assert '"这些/它们/上面提到的"指上一轮的结果集或主体' in prompts.UNDERSTAND_MULTI_SYSTEM
    assert "只补全语义，不要新增或篡改条件" in prompts.UNDERSTAND_MULTI_SYSTEM
    # 既有输出约定保值
    assert "只输出补全后的问题（一行），不要解释，不要重复历史。" in (
        prompts.UNDERSTAND_MULTI_SYSTEM
    )


def test_summarize_factuality_rules() -> None:
    assert "数值结论以所给统计与可见数据行为准，不得外推到不可见数据" in (
        prompts.SUMMARIZE_SYSTEM
    )
    assert "必须如实说明未执行写操作，不得声称删除/更新/插入成功" in prompts.SUMMARIZE_SYSTEM
    # 既有 JSON 格式约定保值
    assert '{"summary":"一句话总结","insights":[{"type":"类型","text":"发现"}]}' in (
        prompts.SUMMARIZE_SYSTEM
    )


def test_fullwidth_discipline_no_stray_halfwidth_comma() -> None:
    for text in (
        prompts.SQL_GEN_SYSTEM,
        prompts.UNDERSTAND_MULTI_SYSTEM,
        prompts.UNDERSTAND_SINGLE_SYSTEM,
        prompts.SUMMARIZE_SYSTEM,
    ):
        assert not re.findall(r"[一-鿿],[一-鿿]", text)
        assert "\\n" not in text  # 字面反斜杠 n=转义失败,应为真换行
