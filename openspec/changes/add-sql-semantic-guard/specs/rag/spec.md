# Spec Delta(rag)

## ADDED Requirements

### Requirement: 召回响应携带结构化 Schema 图

`/internal/rag/retrieve` 的响应 SHALL 在既有 `schema_hint` 之外追加纯增的
结构化 Schema 图:表、列、主键、外键边、可确定的表角色及其来源。图 SHALL NOT
包含连接凭证或连接地址。图无法组装时,响应 SHALL 保持既有字段与语义,缺失
图不构成召回失败。

#### Scenario: 召回返回结构化图

- GIVEN 某租户+数据源有可用的表说明、主键/外键内省或关系数据
- WHEN 召回执行
- THEN 响应携带与 `schema_hint` 表集合一致的结构化图
- AND 图包含表、列、主键/外键边,不包含凭证

#### Scenario: 图组装失败保持旧响应

- GIVEN 图组装因关系/元数据缺失或异常失败
- WHEN 召回执行
- THEN `schema_hint` 与既有响应字段照常返回
- AND 调用方按旧路径继续,不因缺少图被拒绝

#### Scenario: 角色歧义不写死

- GIVEN 某表同时具备事件与金额特征,无法唯一确定角色
- WHEN 图组装
- THEN 该表角色标记为未知或省略
- AND 不按表名或 Sakila 专属名称猜测角色

### Requirement: 外键图驱动桥接表补入

召回主结果确定后,系统 SHALL 按结构化图从已召回表出发补入问题所需的桥接表:
问题需要门店/员工等维度且事件表自带相应外键时,SHALL 优先沿事件表外键进入
维表;否则沿图中确定的最短外键路径补入。桥接表 SHALL 以尾部追加、去重、受
独立预算约束进入 `schema_hint`,并在 `relations_hint` 携带 JOIN 路径。无外键
路径 SHALL 零扩展,SHALL NOT 按表名或列名猜测关系。

#### Scenario: 事件到员工到门店桥接

- GIVEN 召回集含事件表,事件表外键指向员工表,员工表外键指向门店表
- WHEN 问句需要按门店统计事件
- THEN 员工表和门店表按外键路径补入
- AND schema_hint 尾部包含桥接表,relations_hint 包含两条 JOIN 路径

#### Scenario: 无路径不猜测

- GIVEN 图中没有从已召回表通往目标维度的外键路径
- WHEN 补入执行
- THEN 不补入任何表,不按名称/字符串相似度猜测关系

#### Scenario: 预算与主召回优先

- GIVEN 桥接路径数量超过图扩展预算
- WHEN 补入执行
- THEN 只补预算内路径,主召回表不被挤掉
- AND 超预算的扩展按与问题实体距离裁剪,结果可复现

#### Scenario: 图缺失零回归

- GIVEN 召回响应没有结构化图
- WHEN 桥接执行
- THEN 不新增扩展,既有关系召回与 schema_hint 行为保持不变
