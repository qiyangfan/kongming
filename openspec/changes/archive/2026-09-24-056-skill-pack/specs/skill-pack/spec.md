## ADDED Requirements

### Requirement: Skill Pack 定义

系统 SHALL 支持 Skill Pack(垂类内容包)的定义与存储:每包含
manifest(pack_key/name/version/description)与可选内容段
`glossary`(术语:term/aliases/definition/hint)、`fewshots`(人工黄金
示例:question/sql)、`persona`(回答风格与禁令)、`metrics`(指标口径,
与语义层同形)。包归属 (租户) 维度,内容为纯数据资产,SHALL NOT 含可执行
代码、工具或工作流。fewshots 中的 SQL SHALL 仅作生成提示示例文本,
SHALL NOT 被系统直接执行。

#### Scenario: 包四段录入

- GIVEN 管理员录入一个含 glossary 2 条、fewshots 1 条、persona 1 段的包
- WHEN 保存
- THEN 包按 pack_key 与 version 持久化,四段结构完整可读
- AND fewshot SQL 不进入任何执行队列

### Requirement: 按数据源启停

系统 SHALL 按 (包, 数据源) 粒度启停:仅 `enabled` 的绑定参与注入。
管理端点 SHALL 受权限 `system:skill:manage` 保护;跨租户包标识 SHALL
404 同形(不暴露存在性)。启用含 `metrics` 段的包时 SHALL 经语义层登记
指标;metric_name 冲突时 SHALL 拒绝启用且不产生半开状态(包仍为停用)。
禁用或退休包 SHALL NOT 自动删除已登记的语义层指标。

#### Scenario: 启停控制注入

- GIVEN 某包绑定数据源 D 且 enabled=true
- WHEN 在 D 上发起问答
- THEN 该包内容段进入生成投影
- WHEN 将绑定置 enabled=false 后再发起问答
- THEN 注入消失,链路回到无该包形态

#### Scenario: metrics 冲突拒绝启用

- GIVEN 包内 metric_name 与语义层既有指标重名
- WHEN 尝试启用该包
- THEN 启用失败,包保持停用,既有指标不被覆盖

### Requirement: 生成链路投影

加载器 SHALL 合并当前 (租户, 数据源) 全部 enabled 包并截断封顶
(glossary ≤50 条、fewshots ≤10 条、persona ≤800 字),投影为术语段、
黄金示例段、人设段,注入生成/总结语义段。黄金示例段 SHALL 带禁令前缀
(示例仅风格参考,表名列名不可照抄)。零 enabled 包或投影段为空时,生成
链路行为 SHALL 与引入前逐字一致(零回归)。persona 段 SHALL 只影响表述
风格与禁令,SHALL NOT 改变表范围纪律、只读约束与安全策略。

#### Scenario: 多包合并封顶

- GIVEN 同一数据源启用 2 个包,glossary 合计 80 条
- WHEN 投影
- THEN 术语段封顶 50 条,且不影响 fewshots/persona 段

#### Scenario: 零包零回归

- GIVEN 无任何 enabled 包
- WHEN 在任意数据源问答
- THEN 生成链路行为与引入前逐字一致

#### Scenario: 人设不破坏安全纪律

- GIVEN 包 persona 写有「尽量直接给出结论」
- WHEN 问题所需表不在可用表结构内
- THEN 仍按表范围纪律输出如实短路,不因人设臆测编表

### Requirement: Skill Pack 管理

系统 SHALL 提供管理面端点:包列表/详情/创建/更新/退休、绑定启停。
操作 SHALL 落操作日志。列表 SHALL 按当前租户过滤;包内容查询 SHALL
受租户双过滤,跨租户读取 SHALL 404 同形。控制面保留表清单 SHALL 含
`skill_` 前缀表(与 sys_/chat_ 同哲学),数据面执行路由 SHALL 拒绝引用。

#### Scenario: 管理鉴权与租户隔离

- GIVEN 用户无 system:skill:manage
- WHEN 调用包创建
- THEN 返回 403
- AND 他租户包 id 的更新返回 404 同形

#### Scenario: 数据面拒读 skill 表

- GIVEN 语句 `SELECT * FROM skill_pack` 或 `SELECT glossary FROM
  public.skill_pack`
- WHEN 到达执行路由
- THEN verdict=BLOCKED(reason 含 RESERVED_TABLE),落审计
