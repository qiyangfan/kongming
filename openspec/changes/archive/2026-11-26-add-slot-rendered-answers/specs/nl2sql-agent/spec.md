# Spec Delta(nl2sql-agent)

## REMOVED Requirements

### Requirement: 派生量接地与数值白名单

**Reason**: 白名单需枚举任意租户/数据源中全部合法数字样片段(问句前提、含数字标签、日期、编码),开放集合不可穷举;实测已造成正确回答被整句删除。

**Migration**: 由「答案数值的槽位渲染契约」承接:可信数值源不变(结果集/stats/计算值/结构性常量),表达方式由文本比对改为槽位引用;问句前提数字成为独立来源。

### Requirement: 确定性数值审计

**Reason**: 句级剥离在单句回答上会删空全部内容并回落为对结果集的统计罗列,答非所问且剥离计数不可观测。

**Migration**: 由「输出契约违约兜底与可观测」承接:违约整段弃用并进入确定性兜底分层,违约信息计入 trace;不重试模型。

## MODIFIED Requirements

### Requirement: 摘要事实性接地

摘要 SHALL 事实性接地:summarize 喂给 LLM 的上下文 SHALL 含结果行数、本地确定性统计(stats)与前若干行预览,不只给行预览;summary 与 insights 的数值结论 SHALL 以所给统计与可见行为准,SHALL NOT 外推到不可见数据。

#### Scenario: 聚合结果结构化洞察

- GIVEN 查询返回分组聚合结果
- WHEN summarize 节点执行
- THEN 返回 JSON 含 summary + insights(数值经槽位引用,如 [{"type": "max", "text": "{{argmax.count.label}} 最多,{{argmax.count.value}} 部"}])+ stats
- AND 前端渲染洞察卡片(不只一句话)

#### Scenario: 明细结果数据质量提示

- GIVEN 查询返回明细结果且某列有空值
- WHEN summarize 节点执行
- THEN insights 含 {type: "quality", text: "email 列有 {{nulls.email}} 个空值"} 类提示,数量经槽位渲染

#### Scenario: LLM 返回非 JSON 降级

- GIVEN summarize 节点 LLM 返回纯文本
- WHEN 解析失败
- THEN 该文本经输出契约校验:合规则作为 summary,含字面数字或未知槽位则弃用并走确定性兜底;不报错

#### Scenario: 摘要上下文含行数与统计接地

- GIVEN 查询返回 50 行分组聚合结果
- WHEN summarize 节点构造 LLM 上下文
- THEN 上下文含结果行数、本地计算的数值列统计(max/min/avg)与前 5 行预览

#### Scenario: 数值结论不外推(005 冒烟观察)

- GIVEN 结果集 >5 行,前 5 行不含全局最大值
- WHEN summarize 节点执行
- THEN 摘要中的最值结论以所给统计为准,不以可见前 5 行外推

### Requirement: 计算工具

系统 SHALL 提供确定性计算工具(至少增幅与占比两类),入参为显式数值,输出确定性
结果并纳入本轮可信数值源(结果集/stats/既有工具值),供槽位渲染引用。入参 SHALL
校验属于本轮可信数值源,不属于则拒绝并返回可纠正错误。计算工具 SHALL 为只读
无副作用,不写任何业务数据。

#### Scenario: 入参白名单校验

- GIVEN 模型以编造基数 50 调用占比计算
- WHEN 工具执行
- THEN 拒绝执行并返回纠正指引(入参不在本轮可信数值源)
- AND 不产生输出值

### Requirement: 趋势与异常类洞察收紧

insights 类型为 trend(趋势)时,SHALL 要求存在不少于 3 期可排序序列且结论数字
经槽位引用;类型为 anomaly(异常)时,定量结论 SHALL 以槽位引用与确定性统计为据。
不满足时 SHALL 不产出该条定量洞察(可保留非定量的数据质量提示)。

#### Scenario: 两期不写趋势

- GIVEN 结果仅 2 个时间点
- WHEN 生成 insights
- THEN 不出现 trend 类「逐月上升」定量断言

## ADDED Requirements

### Requirement: 答案数值的槽位渲染契约

结果智能分析的 summary 与 insights 文本(含融合正文的数据结论部分)中的数值与含
数字标签 MUST 经系统槽位语法引用并由服务端渲染;去除槽位与引用键标记后 SHALL NOT
残留阿拉伯数字(半角/全角)。槽位 SHALL 至少覆盖:结果行数、stats 键、极值标签与值
(argmax/argmin)、结果单元格(行列号)、问句前提数字、计算工具值、业务称谓与空值
计数。槽位清单 MUST 由系统按本轮形态下发;未知键、越界、畸形语法 SHALL 判违约并
fail-closed,SHALL NOT 猜测渲染,SHALL NOT 对自由文本做数值抽取与集合比对。对外
stats 字段口径 SHALL 保持不变。

#### Scenario: 含数字标签经槽位引用

- GIVEN 分组结果为含数字标签(如 PG-13)的评级计数
- WHEN 生成 summary
- THEN 标签与数值均以槽位引用,模型文本无字面数字
- AND 渲染结果为完整答案句,标签与计数原样呈现

#### Scenario: 问句前提数字可复述

- GIVEN 问句为「单笔支付金额超过 100 美元的订单一共有多少条」且结果为 0
- WHEN 生成与渲染
- THEN 阈值经问句数字槽位引用,不判违约

#### Scenario: 未知槽位 fail-closed

- GIVEN 模型输出槽位清单之外的键
- WHEN 契约校验
- THEN 该段文本弃用并进入兜底,不猜测渲染

### Requirement: 标量结果的确定性作答

执行成功且结果为单行单列数值时,summary SHALL 由服务端确定性生成(称谓 + 数值 +
可识别量词),SHALL NOT 调用语言模型复述;称谓与量词不可得时 SHALL 退为无单位的
确定性表述,insights 可为空。

#### Scenario: 计数问题直接作答

- GIVEN 问句「单笔支付金额超过 100 美元的订单一共有多少条」返回单行 0
- WHEN 收口
- THEN summary 为「共 0 条」类确定性文本
- AND 不发生 summarize 模型调用

### Requirement: 输出契约违约兜底与可观测

槽位违约或渲染失败时 SHALL 改用确定性兜底:多行结果输出结果轮廓(行数 + 极值
标签与值)并标注「未生成解读」;SHALL NOT 以 max/min/avg 罗列充当答案,SHALL NOT
回填未校验文本。违约类型、槽位键与兜底层级 SHALL 计入当轮 trace;违约处理 SHALL
NOT 触发语言模型重试、SHALL NOT 阻断 SQL 与结果字段。

#### Scenario: 违约降级不答非所问

- GIVEN 模型文本违反槽位契约
- WHEN 收口
- THEN 输出确定性结果轮廓并标注「未生成解读」
- AND 不出现 max/min/avg 统计三元罗列

#### Scenario: 违约可观测

- GIVEN 任一违约发生
- WHEN 收口
- THEN trace 含违约类型、槽位键与兜底层级
