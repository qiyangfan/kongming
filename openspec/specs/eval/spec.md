# eval 能力域

## Purpose

NL2SQL 评估体系:gold_sql 与生成 SQL 的匹配规则(列等价 + 行集合数值近似,严格分与宽松分并行)、评估基线管理、gold_sql 编写规范,量化问答质量并拦截回归。

## Requirements

### Requirement: 评估匹配规则

宽松规则(黄金指标口径):行集合近似相等(浮点容差 1e-6,NULL 语义一致)、列名集合等价(gold 列集是生成列集的子集即放行)、gold 含 ORDER BY 时保序比较;严格规则(结果集精确等值)并行输出。基线对照用宽松分,回归红线用严格分不下降。

#### Scenario: 宽松匹配列等价与数值近似
- GIVEN gold_sql 与生成 SQL 待比较
- WHEN 应用宽松规则
- THEN 行集合近似相等(浮点容差 1e-6)
- AND 列名集合相等或 gold 列集为生成列集子集(LLM 多投影语义相关列不扣分)
- AND gold 含 ORDER BY 时保序比较,否则集合比较
- AND 严格分与宽松分并行输出

#### Scenario: gold 投影 ID、生成投影姓名+ID 经宽松放行
- GIVEN gold 投影 ID,生成投影姓名+ID(语义等价)
- WHEN 宽松匹配
- THEN 宽松 PASS

#### Scenario: 生成 SQL 缺 gold 要求的列
- GIVEN 生成 SQL 缺 gold 要求的列
- WHEN 宽松匹配
- THEN 宽松 FAIL

### Requirement: 评估集结构

评估集按租户分文件,每题含 question/gold_sql/expected_tables/category;category 覆盖 single_table/join_2table/join_3table/join_4plus/aggregate/filter 与多轮(turns[])分类;评估请求支持可选 datasourceKey(动态路径用例)。

#### Scenario: 单轮题结构
- GIVEN 评估集文件
- THEN 每租户 15-20 题,覆盖四类基础题型与 join 深度分级
- AND 每题含 question/gold_sql/expected_tables/category

#### Scenario: 多轮题结构
- GIVEN 多轮评估用例
- THEN 每 case 含 turns[](每 turn: question + gold_sql)
- AND 覆盖指代(「这些」)/省略(「再查后10名」)/排序延续/过滤追加四类上下文依赖
- AND 执行时顺序跑各轮,前序问句作 history,每轮独立断言 Execution Match

#### Scenario: 动态数据源用例
- GIVEN 评估请求支持可选 datasourceKey
- THEN 含显式动态路径用例(≥3 题)
- AND 跨租户 datasourceKey 用例预期拒绝(TENANT_FORBIDDEN),验证 fail-closed 不随评估器改动失效

### Requirement: 基线管理

每次评估方法或提示词变更后跑全量,记录严格/宽松/多轮/动态数据源四组基线分数;基线变更须标注原因;黄金指标口径不显著劣化(≥前基线 -5pp),劣化超预期逐题溯源。

#### Scenario: 变更后记录四组基线
- GIVEN 评估方法或提示词变更
- WHEN 跑全量
- THEN 记录严格/宽松/多轮/动态四组分数,写入基线表(标注变更原因)

#### Scenario: 回归红线
- GIVEN 新基线与前一基线并列
- WHEN 黄金指标劣化超 5pp
- THEN 必须逐题溯源(区分评估集改动影响与 LLM 采样波动)后才可合入

### Requirement: gold_sql 编写规范

gold_sql 含 ORDER BY ... LIMIT N 时必须追加唯一列作第二排序键;投影业务可读列(不裸投影 ID);join 新增表必须同步 expected_tables;同题多路径副本必须同步改。

#### Scenario: tiebreaker 必加保证行序确定
- GIVEN gold_sql 含 ORDER BY ... LIMIT N
- WHEN 编写
- THEN ORDER BY 追加唯一列(主键)作第二排序键,保证结果集行序确定(gold 连跑两次结果完全一致)

#### Scenario: 投影业务可读列
- GIVEN 问题问业务实体(客户/商品/会员)
- WHEN 编写 gold_sql
- THEN JOIN 出名称列投影,不裸投影 ID,消除「gen 投影姓名 vs gold 投影 ID」固有边界;名称可能重名时 GROUP BY 主键+名称

#### Scenario: join 新增表同步 expected_tables
- GIVEN gold_sql join 新增表
- WHEN 维护用例
- THEN 必须同步 expected_tables,同题多份副本同步改

#### Scenario: 规范沉淀
- GIVEN 编写规范
- WHEN 沉淀
- THEN 进评估 README,后续新增用例参照执行
