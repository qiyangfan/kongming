"""SQL 自愈错误的分类与修正指令生成。"""

import re


def selfheal_guidance(error: str) -> str:
    """自愈喂回分类:按 PG 错误形态生成针对性修正指令。

    旧行为对一切 does-not-exist 附表哨兵出口——列不存在会被引向弃答
    (表存在,出口是改列);泛化「请修正」使机械错同因连败(
    missing FROM-clause 重试 2 轮同错)。纯函数,优先级自上而下:别名→
    列→关系;哨兵出口仅关系类(表/列以外的 does-not-exist 如函数/类型,
    维持泛化喂回,不误引弃答出口)。
    """
    low = error.lower()
    missing = re.search(r'missing from-clause entry for table "([^"]+)"', low)
    if missing:
        return (
            f"SQL 引用了「{missing.group(1)}」但 FROM/JOIN 中并没有定义这个别名——"
            "为其补充对应表的 JOIN,或改用 FROM/JOIN 中已有的别名；"
            "只引用『可用表结构』清单中存在的表。"
        )
    if "select distinct" in low and "order by" in low:
        # PG 硬约束:DISTINCT 下 ORDER BY 键必须已投影。追问轮最易踩
        # (沿用上轮排序语义 + 关联分类列触发去重)。实测两级演化:模型
        # 先裸同错,采纳子查询后又把外层 DISTINCT 排序键留下——抽象
        # 指令修不动,必须给逐字正确形态模板并明令外层去 DISTINCT。
        return (
            "SELECT DISTINCT 的 ORDER BY 表达式必须出现在投影内,二者不可共存——"
            "正确做法:把「取前 N + 排序」整体放进子查询(排序键在该子查询投影内),"
            "外层直接 JOIN 所需的分类/名称列并沿用子查询携带的排序键,"
            "不再使用 DISTINCT。逐字形态示例:"
            "SELECT x.name, d.name FROM (SELECT e.entity_id, e.name, e.score"
            " FROM source e ORDER BY e.score DESC, e.entity_id LIMIT 5) x"
            " JOIN link l ON l.entity_id = x.entity_id"
            " JOIN dimension d ON d.dim_id = l.dim_id"
            " ORDER BY x.score DESC, x.entity_id；"
            "若确需去重,只按已在投影中的列排序。"
        )
    if re.search(r'column "[^"]+" .*does not exist', low) or (
        low.startswith('column "') and "does not exist" in low
    ):
        return (
            "引用的列不在所引用表的『可用表结构』清单中——只使用清单内的列名"
            "并核对拼写；若该表确无支撑问句口径的列，不得用其他列近似顶替。"
        )
    if re.search(r'relation "[^"]+" .*does not exist', low) or (
        low.startswith('relation "') and "does not exist" in low
    ):
        return (
            "所引用的表或列不在『可用表结构』清单中——核对清单；"
            "若清单确无所需数据，只输出 NO_RELEVANT_TABLE。"
        )
    return _semantic_guidance(error)


def _semantic_guidance(error: str) -> str:
    """语义预检分类对应的修正指令。"""
    if "COLUMN_NOT_IN_GRAPH" in error:
        return (
            "语义预检指出列不存在：只能使用表中真实存在的列。问句里的业务称谓"
            "不等于列名，不得臆造表中没有的字段；若没有对应列，按主键/编号"
            "分组与排序，或省略该名称列。"
        )
    if "TABLE_NOT_IN_GRAPH" in error:
        return "语义预检指出表不存在:只使用『可用表结构』中列出的表,不得臆造表名。"
    if "BRIDGE_JOIN_LOSS" in error:
        return (
            "语义预检指出内连接桥接会丢基表行:基表到目标维度已有直接外键时,"
            "直接 JOIN 该维度;不要经另一事件/金额表桥接。确需保留无匹配行时"
            "改用 LEFT JOIN,并在结果中披露空维度。"
        )
    return ""
