# Spec Delta

## MODIFIED Requirements

### Requirement: 统一出站通道

所有域的外部调用 SHALL 收口于控制面统一出站通道:鉴权材料装配、超时控制、审计切面、失败分类由通道单点承担,域执行器只做协议适配(请求/响应的域方言翻译)。通道 SHALL 支持按域选择执行器(域执行器映射);未声明专属执行器的域 SHALL 回落既有通道级执行器——既有域的执行路径与行为 MUST 零回归。Agent 面 SHALL 恒经既有 `/internal/tool/execute` 回调触发域工具,MUST NOT 直连任何域端点,MUST NOT 持有或感知域凭证(ADR-002 不动,不新增内部端点)。通道超时 SHALL 按域清单配置(部署级可配);超时按分型归类:连接超时归入连接类失败按其重试语义处理(至多重试一次),读超时归入响应类失败(不重试)。

#### Scenario: 域凭证只在控制面

- **GIVEN** 某域需要 API key
- **WHEN** LLM 经回调调用该域工具
- **THEN** key 仅由控制面出站通道装配使用,Agent 面请求体与观察中不含任何凭证形态

#### Scenario: 超时可配且生效

- **GIVEN** 域清单配置超时 5s,端点 8s 未响应
- **WHEN** 出站调用
- **THEN** 通道按超时中断,失败分类按超时分型:连接超时归连接类(按策略器重试语义至多重试一次),读超时归响应类(不重试)

#### Scenario: 按域执行器路由

- **GIVEN** 网关注册了 knowledge 与 websearch 两域,各自声明专属执行器
- **WHEN** 分别执行两域工具
- **THEN** 各域出站由其专属执行器承担(请求形态/鉴权头按域适配),未声明专属执行器的域仍走通道级执行器,既有域行为逐字段不变

## ADDED Requirements

### Requirement: web 检索域注册与凭证治理

系统 SHALL 提供 `websearch` 工具域(联网检索):域清单端点与出口白名单由部署配置的检索供应商单源派生(白名单精确覆盖该供应商 API 主机,部署 MUST NOT 单独改端点)。域注册 SHALL 采用部署级条件注册:供应商与 API key 均已配置才注册,条件不满足时与「域从未存在」不可区分。供应商缺 key、仅配 key 缺供应商或供应商值非法属部署配置错误:prod SHALL fail-closed 拒启,dev SHALL 显式告警降级,MUST NOT 以「未配置」静默掩盖配置残缺。检索凭证 SHALL 只存控制面环境变量并仅在出站请求头装配(ADR-002 不动)。

#### Scenario: 供应商与 key 齐备注册

- **GIVEN** 部署配置 websearch 供应商与 API key
- **WHEN** 控制面启动
- **THEN** web_search 工具注册,持有 tool:websearch:search 权限的用户可见可用,出站精确指向该供应商 API 主机

#### Scenario: 缺 key 拒启不静默

- **GIVEN** 部署配置了供应商但未配置 API key
- **WHEN** prod 环境启动
- **THEN** fail-closed 校验拒绝启动并显式报配置缺项,而非静默不注册

#### Scenario: 未配置零感知

- **GIVEN** 部署未配置供应商与 API key
- **WHEN** 任意调用方发起问答或直调 web_search 回调
- **THEN** 工具不出现在注入集,直调被拒并落审计——与「域从未存在」不可区分

### Requirement: web 检索域脱敏与观察

域策略器 SHALL 以 allowlist 脱敏响应:工具观察只含检索卡(标题/链接/摘要)与命中计数;评分、追踪参数与白名单外字段 MUST NOT 进入工具观察;链接 SHALL 剥离常见追踪参数。供应商响应归一 SHALL 为确定性的检索卡整形(字段映射收在适配单点);畸形报文 SHALL 显式按响应类失败收口,MUST NOT 静默吐空;空结果 SHALL 如实表达(计数 0)。

#### Scenario: 观察只含检索卡

- **GIVEN** 供应商响应含评分、追踪参数与白名单外字段
- **WHEN** 响应经域策略器脱敏
- **THEN** 工具观察只含标题/链接/摘要检索卡与命中计数,评分与白名单外字段不出现在观察

#### Scenario: 畸形报文显式失败

- **GIVEN** 供应商返回非 JSON 或缺结果列表的报文
- **WHEN** 出站调用
- **THEN** 按响应类失败收口(protocol 文案)且不重试,不静默返回空结果

### Requirement: web 检索域配额与授权

`websearch` 域限流配额 SHALL 独立配置(配额第二维度按域分桶),与其它域配额互不挤占。工具权限点 `tool:websearch:search` SHALL 显式授予制(不进 DEVELOPER 推荐集,SUPER_ADMIN 种子自动覆盖);域治理(清单摘要/启停)沿用既有 system:tool:manage 口径,websearch 域自动出现在治理清单。

#### Scenario: 配额独立分桶

- **GIVEN** 同一用户同一分钟内调满 knowledge 域配额
- **WHEN** 该用户调用 web_search
- **THEN** websearch 域按自身配额独立判定,不因 knowledge 域配额用尽而限流

#### Scenario: 显式授予制

- **GIVEN** 默认角色用户未显式授予 tool:websearch:search
- **WHEN** 发起问答
- **THEN** web_search 不在其授权注入集;显式授予后可见可用
