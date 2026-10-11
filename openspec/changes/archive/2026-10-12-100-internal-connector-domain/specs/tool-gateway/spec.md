# Spec Delta

## ADDED Requirements

### Requirement: 内部系统连接器域注册与服务凭证

系统 SHALL 提供 `ticket` 工具域(内部系统连接器首接:工单):域端点与服务
凭证由部署配置单源派生,采用部署级条件注册——端点与凭证均配置才注册,
条件未满足时与「域从未存在」不可区分;配置残缺(仅端点无凭证或反之,
prod 环境)SHALL fail-closed 拒启并显式报缺项,dev 显式告警降级(与
websearch 域同口径)。服务凭证 SHALL 只存控制面环境变量并仅在出站请求装配
(ADR-002 不动),MUST NOT 落库、MUST NOT 入日志与审计明文。工单系统 SHALL
按「最小工单 API 约定」对接(列表/单查/创建三端点形态,约定文档化),
适配字段映射收在适配单点;MUST NOT 为对接差异在域外散落第二套映射。

#### Scenario: 端点与凭证齐备注册

- **GIVEN** 部署配置工单系统端点与服务凭证
- **WHEN** 控制面启动
- **THEN** ticket 域注册,其工具经授权集注入,域出现在治理清单

#### Scenario: 未配置零感知

- **GIVEN** 部署未配置端点与凭证
- **WHEN** 任意调用方发起问答或直调工单工具回调
- **THEN** 工具不出现在注入集,直调被拒并落审计——与「域从未存在」不可区分

#### Scenario: prod 配置残缺拒启

- **GIVEN** prod 部署仅配置端点未配置凭证
- **WHEN** 控制面启动
- **THEN** fail-closed 拒启并显式报缺项,不静默不注册

### Requirement: 预置工具语义与响应标准化

ticket 域 SHALL 预置三个工具:`ticket_search`(关键词/状态/上限入参,返回
标准化工单卡列表与命中计数)、`ticket_get`(id 入参,返回标准化详情卡,
不存在按响应类失败如实表达)、`ticket_create`(标题/描述/优先级入参,
**写工具,缺省需确认**,执行前走既有写工具确认流,确认前 MUST NOT 出站)。
工单卡 SHALL 为 allowlist 字段(标识/标题/状态/优先级/负责人/时间戳),
白名单外字段与内部系统元数据 MUST NOT 进工具观察;状态/优先级枚举值
SHALL 原样透传(MUST NOT 平台侧翻译语义)。参数校验按预置声明执行;
连接级失败重试至多一次,响应类错误不重试;空结果如实表达(计数 0)。

#### Scenario: 检索返回标准化工单卡

- **GIVEN** 工单系统有 3 条匹配工单,报文含白名单外内部字段
- **WHEN** LLM 调用 ticket_search
- **THEN** 观察只含 3 张标准化工单卡与命中计数,内部字段不出现在观察

#### Scenario: 创建工单走确认流

- **GIVEN** LLM 准备调用 ticket_create
- **WHEN** 用户未确认
- **THEN** 不出站、工单系统无新单;确认后才执行并如实返回结果

#### Scenario: 单查不存在如实失败

- **GIVEN** 工单 id 不存在
- **WHEN** 调用 ticket_get
- **THEN** 按响应类失败返回友好「不存在」观察,不编造、不静默吐空

### Requirement: ticket 域授权、审计与配额

`tool:ticket:search`(search/get)与 `tool:ticket:manage`(create)SHALL
显式授予制(不进默认角色推荐集,SUPER_ADMIN 种子自动覆盖);未授权工具
不出现在注入集、直调被拒并落审计。每次域工具执行 SHALL 落既有工具审计表
(域标签=ticket,记录工具名/参数摘要/成败分类/耗时,同表同语义);create
执行 SHALL 在审计参数摘要中记录目标工单标识(创建结果回填)。ticket 域
限流配额 SHALL 独立分桶,与其它域互不挤占。既有域与内置工具行为
MUST 零回归。

#### Scenario: 未授权不可见

- **GIVEN** 用户未获 ticket 工具权限点
- **WHEN** 发起问答
- **THEN** 工单工具不在注入集,直调被拒并落审计

#### Scenario: 配额独立分桶

- **GIVEN** 同一用户调满其它域配额
- **WHEN** 该用户调用 ticket_search
- **THEN** ticket 域按自身配额独立判定,不受其它域挤占

#### Scenario: 既有域零回归

- **GIVEN** websearch 域与内置工具任一既有场景
- **WHEN** ticket 域引入后执行
- **THEN** 行为与引入前逐字段一致
