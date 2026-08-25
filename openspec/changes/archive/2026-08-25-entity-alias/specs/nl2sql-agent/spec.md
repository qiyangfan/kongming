## MODIFIED Requirements

### Requirement: 结果智能分析(结构化洞察)

summarize 节点输出结构化洞察:summary(一句话)+ insights[](聚合洞察 max/min/ratio、数据质量提示)+ stats;LLM 返回非 JSON 时降级为纯文本。

摘要 SHALL 事实性接地:summarize 喂给 LLM 的上下文 SHALL 含结果行数、本地确定性统计(stats)与前若干行预览,不只给行预览;summary 与 insights 的数值结论 SHALL 以所给统计与可见行为准,SHALL NOT 外推到不可见数据;用户请求写操作而实际执行的是只读查询时,摘要 SHALL 如实说明未执行写操作,SHALL NOT 声称删除/更新/插入成功。

摘要 SHALL 称谓对齐:summarize 喂给 LLM 的上下文 SHALL 含所涉表的业务称谓别名(alias,随召回 schema_hint 段注入);summary 与 insights 提及数据实体时 SHALL 使用业务称谓——用户用语优先,其次所给别名;SHALL NOT 只写物理表名,SHALL NOT 出现 schema 全限定名;首次提及可以「业务名(物理名)」并注;别名缺失时回退表说明中的中文名称;SHALL NOT 编造别名(与「数值结论不外推」同构的事实性约束)。

#### Scenario: 聚合结果结构化洞察
- GIVEN 查询返回分组聚合结果
- WHEN summarize 节点执行
- THEN 返回 JSON 含 summary + insights(如 [{type: "max", text: "动作类最多 120 部"}])+ stats
- AND 前端渲染洞察卡片(不只一句话)

#### Scenario: 摘要上下文含行数与统计接地
- GIVEN 查询返回 50 行分组聚合结果
- WHEN summarize 节点构造 LLM 上下文
- THEN 上下文含结果行数、本地计算的数值列统计(max/min/avg)与前 5 行预览

#### Scenario: 数值结论不外推(005 冒烟观察)
- GIVEN 结果集 >5 行,前 5 行不含全局最大值
- WHEN summarize 节点执行
- THEN 摘要中的最值结论以所给统计为准,不以可见前 5 行外推

#### Scenario: 实体称谓以业务名表达
- GIVEN 用户提问「商品表有多少种商品」(goods 表 alias=「商品表」已注入上下文)
- WHEN summarize 节点执行
- THEN 摘要称「商品表」而非 goods;不出现 tenant_default.goods 全限定名;首次提及可「商品表(goods)」并注

#### Scenario: 用户用语优先于别名
- GIVEN 用户称某表为「订单主表」而 alias 为「订单表」
- WHEN summarize 节点执行
- THEN 摘要沿用用户的「订单主表」,不被别名带偏

#### Scenario: 写操作请求摘要不谎称成功(005/006 冒烟观察)
- GIVEN 用户问题为「删除 name 为 X 的商品」,实际执行为只读 SELECT
- WHEN summarize 节点执行
- THEN 摘要说明未执行写操作(只读查询返回了相关数据现状),不声称「删除成功」

#### Scenario: 明细结果数据质量提示
- GIVEN 查询返回明细结果且某列有空值
- WHEN summarize 节点执行
- THEN insights 含 {type: "quality", text: "email 列有 3 个空值"}

#### Scenario: LLM 返回非 JSON 降级
- GIVEN summarize 节点 LLM 返回纯文本
- WHEN 解析失败
- THEN 退化为 summary=该文本,insights/stats 为空,不报错
