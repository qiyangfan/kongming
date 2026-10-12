# Spec Delta

## MODIFIED Requirements

### Requirement: 指标口径定义

系统 SHALL 支持按数据源定义指标:metric_name(数据源内唯一)、sql_template(口径表达式/片段)、business_terms(业务别名/触发词)、enabled。sql_template SHALL NOT 被直接执行——仅作文字注入 prompt(最终 SQL 由 LLM 生成并走防火墙+只读池全量防御);管理端点不提供任何「执行模板」能力。指标定义 SHALL 携带可见范围:`visible_scope ∈ {'ALL','DEPT'}`(缺省 ALL,存量零回归)与 `dept_id`(DEPT 档必填且须为存在部门,ALL 档强制为空);visible_scope=DEPT 而 dept_id 缺失的创建/更新 SHALL 被文案级拒绝。管理端点(列表/详情/创建/更新/启停)SHALL 保持全量视角,不做可见性过滤(管理者治理面,与知识域 102 同口径)。

#### Scenario: 指标定义与唯一约束

- GIVEN 数据源已存在指标「复购率」
- WHEN 创建同名指标
- THEN 拒绝(唯一约束),或经编辑更新原定义

#### Scenario: 可见范围字段管理语义(110)

- GIVEN 管理者创建指标并选择 visible_scope=DEPT
- WHEN 未选归属部门提交
- THEN 文案级拒绝,不落库
- AND 选择部门后保存成功,列表与详情回显 visible_scope 与 dept_id
- WHEN 将该指标改回 visible_scope=ALL
- THEN dept_id 被置空,不残留

### Requirement: 生成链路注入

RAG 召回响应 SHALL 纯增:relations_hint(仅召回表集合内的两两 JOIN path 文字描述;召回为空则不携带;召回表集合含 095 关系扩展结果)与 metric_hints(问句命中 business_terms/metric_name 的启用指标,top-5,含 sql_template 文字);Agent 面 generate 节点 SHALL 在用户提示中拼接两段(系统提示模板不变),并遵循「JOIN 优先依据 relations_hint、口径优先依据 metric_hints」的引导;无数据时零注入,行为与引入前逐字一致。

**076 修订(结构化命中透出)**:召回响应除既有文字段外 SHALL 纯增 `metric_hits` 结构化列表(metric_id / metric_name / sql_template / updated_at);文字段 SHALL 由该列表派生(单一来源,杜绝两处口径漂移)。

**095 修订(收窄基准定义澄清)**:relations_hint 的收窄基准「召回表集合」SHALL 含 095 关系感知召回扩展补入的表——扩展表不带 JOIN path 注入则对生成无增益。本修订只改基准定义,语义层管理端点(关系 CRUD/introspection/指标 CRUD)行为零变化。

**110 修订(指标可见性按 data_scope 过滤)**:metric_hints/metric_hits 的候选集 SHALL 先过调用者可见性再进入命中匹配——调用者有效范围取其启用角色的最宽松档(most_permissive):ALL 档调用者 SHALL 见全部启用指标;DEPT/DEPT_AND_BELOW/CUSTOM 档调用者 SHALL 见「visible_scope='ALL'」档与「visible_scope='DEPT' 且 dept_id ∈ 调用者部门集合」档(DEPT=本部门、DEPT_AND_BELOW=递归子部门并集、CUSTOM=勾选部门并集);SELF、无调用者身份、用户不存在或无启用角色 SHALL 仅见 ALL 档。可见性判定 SHALL 下推仓储 SQL 单点(与 102 同判纪律);CUSTOM 空部门集合 SHALL 按读面语义降级为「仅 ALL 档」(不 fail-closed、不阻断召回主链路,与 083 执行面语义分层)。本修订 MUST NOT 改变 076「文字段由结构化命中派生」语义;enrich 位于检索上下文缓存之外,缓存键零改动。

#### Scenario: 注入收窄到召回集合

- GIVEN 数据源有 20 张表的关系,本次召回 2 表
- WHEN 召回响应组装
- THEN relations_hint 只含该 2 表相关的关系,不灌全库

#### Scenario: 指标命中注入

- GIVEN 启用指标「复购率」含 sql_template
- WHEN 用户问「各品类复购率」
- THEN metric_hints 含该指标口径文字,generate 提示携带

#### Scenario: 结构化命中与文字段同源

- GIVEN 一次问句命中了 N 条启用指标
- WHEN 召回响应组装
- THEN 结构化命中列表含 N 条,每条带指标标识、名称、口径表达式与修订时间
- AND metric_hints 文字段恰为该列表的派生,两侧数量与内容一一对应

#### Scenario: 扩展表参与收窄基准(095)

- GIVEN 召回 2 表,095 扩展补入 1 张关联表,该扩展表与某召回表之间存在声明关系
- WHEN 召回响应组装
- THEN relations_hint 含该扩展表与召回表之间的 JOIN path(收窄基准为「主召回 ∪ 扩展」,仍不灌全库)

#### Scenario: 无命中零注入零附加

- GIVEN 问句未命中任何启用指标
- WHEN 召回响应组装
- THEN metric_hints 为空、结构化命中为空列表,注入行为与引入前逐字一致

#### Scenario: ALL 调用者全量可见(110)

- GIVEN 存在 visible_scope=DEPT(部门 A)与 visible_scope=ALL 两枚启用指标,问句同时命中两者
- WHEN 有效范围=ALL 的调用者发起召回
- THEN metric_hints/metric_hits 含两枚指标

#### Scenario: DEPT 调用者按部门集合过滤(110)

- GIVEN 同上两枚指标,调用者有效范围=DEPT 且部门属于 A
- WHEN 发起召回
- THEN 命中面含两枚;调用者有效范围=DEPT 且部门属于 B 的调用者仅见 ALL 档指标

#### Scenario: SELF 与无身份仅见公共档(110)

- GIVEN 存在 DEPT 档启用指标,问句命中它
- WHEN SELF 档调用者、或不携带 user_id 的内部调用发起召回
- THEN metric_hints/metric_hits 均不含该指标,ALL 档指标命中不受影响

#### Scenario: CUSTOM 空集合读面降级(110)

- GIVEN 调用者角色为 CUSTOM 且勾选部门集合为空
- WHEN 发起召回,问句命中 DEPT 档指标
- THEN 该指标不注入,ALL 档指标正常注入,召回主链路不抛错不阻断
