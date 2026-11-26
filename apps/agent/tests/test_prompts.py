"""检查 SQL 生成提示中的关键规则和中文标点要求。"""

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


def test_sql_gen_projection_id_discipline() -> None:
    """投影 id 收敛:技术主键默认不进投影+业务编号列优先+DISTINCT 排序例外。"""
    assert "技术主键等代理键（主键 id 类列）默认不进投影" in prompts.SQL_GEN_SYSTEM
    assert "行标识需要编号时优先业务编号列" in prompts.SQL_GEN_SYSTEM
    assert "用户明确点名 ID/编号时才投影" in prompts.SQL_GEN_SYSTEM
    assert "DISTINCT 去重查询例外" in prompts.SQL_GEN_SYSTEM
    assert "排序键只能取已在投影中的列" in prompts.SQL_GEN_SYSTEM
    assert "❌ DISTINCT 去重查询追加投影外主键排序" in prompts.SQL_GEN_SYSTEM
    # 与收紧口径冲突的历史措辞不得出现
    assert "ID 可附加，名称列必须在投影中" not in prompts.SQL_GEN_SYSTEM
    # 主句要求把名称列加入投影,并使用它作为平分时的排序条件。
    assert "名称列必须在投影中" in prompts.SQL_GEN_SYSTEM
    assert "排序键可能并列时必须追加主键等唯一列作第二排序键" in prompts.SQL_GEN_SYSTEM


def test_sql_gen_column_level_sentinel() -> None:
    """规则 10 列级哨兵:问句口径所需列不存在且无法推导时如实说明,
    不得以其他列近似顶替(表级哨兵语义补全至表/列两级)。"""
    assert "问句指定口径所需的列" in prompts.SQL_GEN_SYSTEM
    assert "不在该段中且无法由现有列合理推导" in prompts.SQL_GEN_SYSTEM
    assert "不得用其他列近似顶替" in prompts.SQL_GEN_SYSTEM
    assert "用名称列冒充价格排序同样是错误答案" in prompts.SQL_GEN_SYSTEM
    assert "缺支撑问句口径的关键列" in prompts.SQL_GEN_SYSTEM
    assert "不要硬凑近似答案" in prompts.SQL_GEN_SYSTEM
    assert "❌ 问句口径所需列不在表结构中时用其他列硬凑" in prompts.SQL_GEN_SYSTEM
    # 哨兵出口与规则 7 的例外条件应保留在 prompt 中。
    assert "唯一例外于规则 7——只输出 NO_RELEVANT_TABLE" in prompts.SQL_GEN_SYSTEM
    assert "不要解释、不要编造表名或列名" in prompts.SQL_GEN_SYSTEM


def test_sql_gen_partition_and_event_metric_rules() -> None:
    """分区/继承子表用父表;业务事件笔数与金额口径不混用。"""
    assert "分区/继承子表纪律" in prompts.SQL_GEN_SYSTEM
    assert "一律用父表" in prompts.SQL_GEN_SYSTEM
    assert "子表可能为空，用子表会产生假零结果" in prompts.SQL_GEN_SYSTEM
    assert "不得用金额明细的行数冒充事件笔数" in prompts.SQL_GEN_SYSTEM
    assert "以事件明细的发生时间与归属维度驱动" in prompts.SQL_GEN_SYSTEM
    assert "❌ 用子表/分片表代替父表" in prompts.SQL_GEN_SYSTEM
    assert "❌ 问“业务事件笔数”却用金额明细的行数冒充" in prompts.SQL_GEN_SYSTEM


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
    assert "数值结论以所给统计与可见数据行为准，不得外推到不可见数据" in (prompts.SUMMARIZE_SYSTEM)
    assert "必须如实说明未执行写操作，不得声称删除/更新/插入成功" in prompts.SUMMARIZE_SYSTEM
    # 既有 JSON 格式约定保值
    assert '{"summary":"一句话总结","insights":[{"type":"类型","text":"发现"}]}' in (
        prompts.SUMMARIZE_SYSTEM
    )


def test_summarize_numeric_grounding_rules() -> None:
    """白名单/派生量禁心算/trend 门槛已入 SUMMARIZE_SYSTEM。"""
    assert "槽位" in prompts.SUMMARIZE_SYSTEM
    assert "禁止出现任何阿拉伯数字" in prompts.SUMMARIZE_SYSTEM
    assert "派生量不得心算写入" in prompts.SUMMARIZE_SYSTEM
    assert "宁省略，不编造" in prompts.SUMMARIZE_SYSTEM
    assert "type=trend 仅在结果不少于 3 期" in prompts.SUMMARIZE_SYSTEM


def test_summarize_alias_alignment_rules() -> None:
    """称谓对齐规则在位,且与事实性规则共存(互锁不互斥)。"""
    assert "称谓对齐规则：提及数据实体时必须使用业务称谓别名(alias)" in (prompts.SUMMARIZE_SYSTEM)
    assert "用户用语优先，其次所给别名" in prompts.SUMMARIZE_SYSTEM
    assert "禁止只写物理表名" in prompts.SUMMARIZE_SYSTEM
    assert "禁止出现schema全限定名" in prompts.SUMMARIZE_SYSTEM
    assert (
        "首次提及可「业务名(物理表名)」并注(如「业务实体表(entity_table)」，"
        "物理表名即英文表名)" in prompts.SUMMARIZE_SYSTEM
    )
    assert "别名缺失时回退表说明中的中文名称" in prompts.SUMMARIZE_SYSTEM
    assert "禁止编造别名" in prompts.SUMMARIZE_SYSTEM
    # 双规则共存(事实性 + 称谓)
    assert "不得外推到不可见数据" in prompts.SUMMARIZE_SYSTEM


def test_sql_gen_table_scope_discipline() -> None:
    """规则 10:表范围纪律——表/列仅来自可用表结构;哨兵是规则 7 唯一例外。"""
    assert "表范围纪律" in prompts.SQL_GEN_SYSTEM
    assert "必须只来自「可用表结构」段" in prompts.SQL_GEN_SYSTEM
    assert "不得引入其中不存在的表" in prompts.SQL_GEN_SYSTEM
    assert "唯一例外于规则 7——只输出 NO_RELEVANT_TABLE" in prompts.SQL_GEN_SYSTEM
    # 数词描述与 token 实际数量不符会干扰模型。
    assert "六个词" not in prompts.SQL_GEN_SYSTEM
    assert "不要解释、不要编造表名或列名" in prompts.SQL_GEN_SYSTEM


def test_fullwidth_discipline_no_stray_halfwidth_comma() -> None:
    for text in (
        prompts.SQL_GEN_SYSTEM,
        prompts.UNDERSTAND_MULTI_SYSTEM,
        prompts.UNDERSTAND_SINGLE_SYSTEM,
        prompts.SUMMARIZE_SYSTEM,
    ):
        assert not re.findall(r"[一-鿿],[一-鿿]", text)
        assert "\\n" not in text  # 字面反斜杠 n=转义失败,应为真换行


def test_sql_gen_aggregate_followup_single_row() -> None:
    """聚合追问:整体总量/总额/合计输出单一聚合行,不得逐行明细。"""
    assert '追问整体总量/总额/合计（"总共/合计/一共"）时输出单一聚合行' in (prompts.SQL_GEN_SYSTEM)
    assert "不得逐行明细" in prompts.SQL_GEN_SYSTEM


def test_sql_gen_correspondence_distinct() -> None:
    """对应关系去重:一对多 JOIN 后投影组合重复必须 DISTINCT(靶点)。"""
    assert "「对应关系/名录/哪些」类明细经一对多 JOIN 后投影列组合重复" in (prompts.SQL_GEN_SYSTEM)
    assert "重复的对应行是噪声" in prompts.SQL_GEN_SYSTEM
    assert "❌ 「对应关系/名录」类查询经一对多 JOIN 后不去重" in prompts.SQL_GEN_SYSTEM


def test_understand_aggregate_followup_explicit() -> None:
    """understand 对聚合追问的补全必须显式单一汇总口径。"""
    assert "单一汇总口径" in prompts.UNDERSTAND_MULTI_SYSTEM
    assert "不得补全为逐行明细口径" in prompts.UNDERSTAND_MULTI_SYSTEM
    assert "完整复述该结果集的构造条件" in prompts.UNDERSTAND_MULTI_SYSTEM
    assert '也不得用其他集合（如"最近的订单"）替换' in prompts.UNDERSTAND_MULTI_SYSTEM
    # prompt 应保留原始数值规则和输出格式要求。
    assert "补全必须保留上一轮的全部限定条件（筛选/排序/数量限制/聚合口径）" in (
        prompts.UNDERSTAND_MULTI_SYSTEM
    )
    assert "只输出补全后的问题（一行），不要解释，不要重复历史。" in (
        prompts.UNDERSTAND_MULTI_SYSTEM
    )


def test_doc_answer_citation_discipline() -> None:
    """检查文档回答提示中的引用键标注和引用边界。"""
    from kongming_agent.graph.doc_prompt import DOC_ANSWER_SYSTEM

    # 规则 2:句末标注【引用键】且与下发来源逐字一致
    assert "【引用键】" in DOC_ANSWER_SYSTEM
    assert "逐字一致" in DOC_ANSWER_SYSTEM
    # 规则 4:禁包装形态、禁自造未下发键
    assert "来源：" in DOC_ANSWER_SYSTEM  # 禁令点名(全角冒号)
    assert "SHALL NOT" in DOC_ANSWER_SYSTEM
    # 退役形态:文档名 §段落标注法不得出现
    assert "[文档名 §段落]" not in DOC_ANSWER_SYSTEM


def test_prompt_examples_are_schema_agnostic() -> None:
    """提示词示例不得固化某个库/表/字段;只保留通用形态示例。"""
    for token in ("film_id", "rental_rate", "film_category", "tenant_default.goods"):
        assert token not in prompts.SQL_GEN_SYSTEM
    assert "goods" not in prompts.SUMMARIZE_SYSTEM
    assert "回答措辞以用户原始问题为准" in prompts.SUMMARIZE_SYSTEM
