# eval 能力域

## Purpose

NL2SQL 评估体系:gold_sql 与生成 SQL 的匹配规则(列等价 + 行集合数值近似,严格分与宽松分并行)、评估基线管理、gold_sql 编写规范,量化问答质量并拦截回归。

## Requirements

### Requirement: 评估运行形态

评估器 SHALL 以进程外工具形态对真实链路运行:经登录凭证调用用户问答端点
(完整链路:认证→租户→RAG 召回→生成→执行→洞察),以 persist=false 临时对话
形态执行(不落会话/消息,不触发标题改写);gold_sql 由评估器直连目标库执行,
生成 SQL 取自链路响应。评估器与常规测试门禁隔离(SHALL NOT 进入无评估环境的
默认收集),评估分层 smoke(约 5 题)/full(全量单轮)/multiturn(多轮)三级
显式选择运行。

#### Scenario: 评估走真实用户链路

- **GIVEN** 评估环境(双进程+数据集+凭证)就绪
- **WHEN** 运行评估
- **THEN** 生成侧经用户问答端点获得(登录态+租户头+数据源 key 与真实用户同路径)
- **AND** 生成 SQL 取自链路响应,gold_sql 直连目标库执行,双侢单独比较
- **AND** 评估会话不落库(persist=false),不污染业务数据

#### Scenario: 默认门禁隔离

- **GIVEN** 无评估环境(无服务/无数据集/无凭证)的常规测试环境
- **WHEN** 运行默认测试收集
- **THEN** 评估用例不被收集执行(评估须显式指定目录与分层运行)

### Requirement: 评估匹配规则

宽松规则(黄金指标口径)为双路径语义比较:路径一=列名集合相等或 gold 列集
⊆ 生成列集(大小写不敏感)→ 生成侧按 gold 列名投影 → 行集合近似相等(浮点
容差 1e-6,NULL 语义一致),gold_sql 含 ORDER BY 时保序比较、否则 multiset
比较;路径二=gold 数值 multiset ⊆ 生成数值 multiset(容忍聚合别名/投影列名
不兼容但数值全对)。纯文本结果集(gold 无数值)不走路径二(空集子集恒真
误判),路径一列名不兼容时回退严格集合等值兜底。已知既定边界:路径二天然
无序——宽松口径不惩罚序错(值集对即放行,保序仅约束路径一);gold 投影
数值 id 列而生成侧缺失该数值时路径二判 fail,靠编写规范「投影业务可读列」
规避。严格规则(结果集 multiset 精确等值)并行输出;基线对照用宽松分,
回归红线用宽松分不下降(劣化 ≥5pp 逐题溯源)。

#### Scenario: 宽松匹配列等价与数值近似

- **GIVEN** gold_sql 与生成 SQL 待比较
- **WHEN** 应用宽松规则
- **THEN** 路径一:列名集合相等或 gold 列集为生成列集子集,生成侧按 gold
    列名投影后行集合近似相等(浮点容差 1e-6)
- **AND** gold 含 ORDER BY 时路径一保序比较,否则集合比较
- **AND** 路径二:gold 数值 multiset ⊆ 生成数值 multiset(列名不兼容救场)
- **AND** 纯文本结果集(gold 无数值)回退严格集合等值(不走路径二)
- **AND** 严格分与宽松分并行输出

#### Scenario: gold 投影 ID、生成投影姓名+ID 经宽松放行

- **GIVEN** gold 投影 ID,生成投影 姓名+ID(列超集)
- **WHEN** 宽松匹配
- **THEN** 宽松 PASS(路径一投影后行集相等)

#### Scenario: 聚合别名差异经路径二放行

- **GIVEN** gold 列 [id, total],生成列 [id, sum](别名差异,数值全对)
- **WHEN** 宽松匹配
- **THEN** 宽松 PASS(路径二数值子集成立)

#### Scenario: 生成 SQL 缺 gold 要求的列

- **GIVEN** 生成 SQL 缺 gold 要求的列(数值也对不上)
- **WHEN** 宽松匹配
- **THEN** 宽松 FAIL

#### Scenario: 序错但值集对时宽松放行(既定边界)

- **GIVEN** gold 含 ORDER BY,生成侧行序不同但值集相同
- **WHEN** 宽松匹配
- **THEN** 宽松 PASS(路径二/纯文本回退天然无序,不惩罚序错;序错差异由
    严格分口径兜底记录)

### Requirement: 评估集结构

评估集按租户分文件,每题含 question/gold_sql/expected_tables/category;category
覆盖 single_table/join_2table/join_3table/join_4plus/aggregate/filter 与多轮(turns[])
分类;评估请求支持可选 datasourceKey(动态路径用例)。gold_sql 结果集 SHALL
≤1000 行(带 LIMIT 或聚合天然小集)——生成侧链路有 1000 行截断上限,超限题
双侧截断口径不一致会假阴性。

#### Scenario: 单轮题结构

- **GIVEN** 评估集文件
- **THEN** 每租户题集 15-20 题(另含 smoke 标记题约 2-5,计入文件但不占
    该区间),覆盖四类基础题型与 join 深度分级
- **AND** 每题含 question/gold_sql/expected_tables/category
- **AND** gold_sql 结果集 ≤1000 行(LIMIT 或聚合天然小集)

#### Scenario: 多轮题结构

- **GIVEN** 多轮评估用例
- **THEN** 每 case 含 turns[](每 turn: question + gold_sql)
- **AND** 覆盖指代(「这些」)/省略(「再查后10名」)/排序延续/过滤追加四类上下文依赖
- **AND** 执行时顺序跑各轮,前序问句作 history,每轮独立断言 Execution Match

#### Scenario: 动态数据源用例

- **GIVEN** 评估请求支持可选 datasourceKey
- **THEN** 含显式动态路径用例(≥3 题)
- **AND** 跨租户 datasourceKey 用例预期拒绝(同形 400 DATASOURCE_INVALID,
    与 chat 域防 key 探测口径一致),验证 fail-closed 不随评估器改动失效

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
