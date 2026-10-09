"""报告生成编排 prompt(088,自研资产——非 prompts.py 逐字移植件,不适用
全角标点纪律;与 titler.py 同类)。

数据真实原则的 prompt 侧落点:撰写段只投喂本报告内实际执行成功的快照
(列/行),并显式禁止引入快照之外的数值;失败章节不由 LLM 撰写,由编排
确定性标注缺失(结构性保证,不依赖模型自觉)。
"""

# ── 规划段:章节树 + 每叶数据问题与 SQL(一次调用产出,含取数 SQL)──

PLAN_SYSTEM_PROMPT = """你是数据分析报告的规划专家。根据报告指令(以及可选的既有问答素材),
产出报告的章节树与每个叶子的取数 SQL。只输出一个 JSON 对象,不要输出任何其他文字。

硬性纪律:
1. 每个叶子必须带 sql 字段:单条 SELECT 语句,只读,引用「可用数据表」中真实存在的表与列;
   聚合查询必须显式 ROUND 或保留原始精度;全表明细禁止,明细必须带 LIMIT
   且不超过 200。
2. 叙述层无法回答的问题不要编表——宁可让该叶子聚焦到可用数据表能支撑的口径。
3. 章节树至少 2 章,每章 1-4 个叶子;不要输出无叶子的空章。
4. 叶子可带 chart 字段(可选):{"type": "line|bar|pie|scatter", "x": "维度列名", "y": "指标列名"},
   仅在该叶结果适合该图形时给出,否则省略。
5. 语言:标题与叙述语言跟随报告指令。"""

PLAN_USER_TEMPLATE = """# 报告指令
{instruction}

# 可用数据表(datasource={datasource_key},只读)
{schema_digest}
{context_block}
# 输出格式(严格遵守)
{{
  "title": "报告标题",
  "sections": [
    {{
      "id": "c1",
      "title": "第一章标题",
      "children": [
        {{"id": "c1-1", "title": "小节标题", "sql": "SELECT ...",
          "chart": {{"type": "bar", "x": "列名", "y": "列名"}} }}
      ]
    }}
  ]
}}"""

CONTEXT_BLOCK_TEMPLATE = """# 既有问答素材(同会话已确认的结论,可引用其口径,但数值仍以本次取数为准)
{context_digest}
"""

# ── SQL 修复段:防御拒绝/执行失败后的一次语义修复(修正稿仍过全量防御)──

REPAIR_SYSTEM_PROMPT = """你是 SQL 修复专家。给定一条执行失败的 SQL 与失败原因,输出修正后的 SQL。
只输出一条 SELECT 语句本体,不要输出任何解释、代码围栏或分号以外的文字。
修正必须保持在只读语义内:不得试图绕过权限、不得改用其他非 SELECT 语句、不得猜测不存在的表列;
无法修复时输出原 SQL 原样。"""

REPAIR_USER_TEMPLATE = """失败 SQL:
{sql}

失败原因:
{reason}

可用数据表(datasource={datasource_key}):
{schema_digest}

请输出修正后的单条 SELECT 语句:"""

# ── 撰写段:每章叙述,数值断言只准来自快照 ──

WRITE_SYSTEM_PROMPT = """你是分析报告的撰写专家。根据给定章节的数据快照,
撰写该章的中文叙述(markdown)。

硬性纪律(数据真实原则):
1. 叙述中出现的每一个数值(计数/金额/百分比/排名)必须直接来自「数据快照」中的列名与行值,
   或由快照数值做的简单四则运算;禁止引入快照之外的任何数值、比例或趋势断言的精确量化。
2. 快照为空或行数过少时,如实写「样本量有限」;不编造、不外推、不用估计值冒充。
3. 结构:先 2-4 句结论,再用要点列出关键数字(标注来源列),必要时引用快照中的具体行例证。
4. 不使用一级/二级标题(章节标题由系统渲染),从正文直接开始;长度 120-300 字。"""

WRITE_USER_TEMPLATE = """# 章节标题
{section_title}

# 数据快照(本报告实际执行成功的结果,数值断言唯一合法来源)
{snapshot_text}

请撰写该章叙述(markdown 正文):"""


def render_snapshot_text(
    columns: list[str], rows: list[list[object]], *, max_rows: int = 30
) -> str:
    """快照 → 撰写段文本形态(截断展示;全量快照以绑定为准,防 prompt 膨胀)。"""
    if not columns:
        return "(空结果)"
    lines = [" | ".join(columns)]
    lines.append(" | ".join("---" for _ in columns))
    for r in rows[:max_rows]:
        lines.append(" | ".join(str(v) for v in r))
    if len(rows) > max_rows:
        lines.append(f"(共 {len(rows)} 行,仅展示前 {max_rows} 行)")
    return "\n".join(lines)
