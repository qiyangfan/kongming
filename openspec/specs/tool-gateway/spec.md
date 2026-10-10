## Purpose

把「SQL 防火墙 + 六层纵深」的单域治理模式推广为**外部工具域接入框架**:域 = 出站能力 + 声明式清单 + 策略器,统一出站通道收口凭证与审计。新工具域的接入从「重造治理代码」变为「声明清单 + 实现策略器」;Agent 面恒经既有工具回调,零凭证纪律与信任边界(ADR-002)零改动。

## Requirements

### Requirement: 域清单与注册

系统 SHALL 以声明式域清单定义外部工具域:每个域 MUST 声明域标识、能力端点(主机与路径形态)、出口主机白名单、超时、重试分类、脱敏策略标识、限流配额与审计域标签。域注册 SHALL 复用部署级条件注册语义(tool-calling 口径):注册条件不满足(端点未配置/白名单主机非法/多租户受限域遇多租户部署)时该域不注册、不注入、不出现在任何清单,与「域从未存在」不可区分。清单装配 SHALL 为可确定性重放的纯函数(同配置同结果);清单畸形(缺必填字段/标识冲突/白名单主机非法)SHALL 使该单域剔除并告警,fail-closed 不裸崩、不影响其余域注册。

#### Scenario: 条件满足注册可见

- **GIVEN** 部署配置了某域端点且白名单主机合法
- **WHEN** 控制面启动
- **THEN** 该域注册成功,其工具经授权集注入对 LLM 可见,执行走统一出站通道

#### Scenario: 条件不满足零感知

- **GIVEN** 某域端点未配置
- **WHEN** 任意调用方发起 ReAct 问答
- **THEN** 该域工具不出现在注入集与清单,未授权回调执行被拒并落审计——与「域从未存在」不可区分

#### Scenario: 畸形清单单域剔除

- **GIVEN** 域清单中一个域缺出口白名单字段,其余两个域合法
- **WHEN** 控制面启动装载
- **THEN** 畸形域被剔除并落告警,其余两域正常注册,进程不崩溃

### Requirement: 域策略器契约

每个域 MUST 绑定一个域策略器,策略器缺失的域 MUST NOT 注册(fail-closed)。策略器 SHALL 承担四项职责,顺序固定:(1) 出口校验——实际出站主机 MUST 命中域白名单,未命中拒绝(021 白名单语义的推广);(2) 参数校验——按域声明的参数约束校验出站入参,越界/畸形拒绝;(3) 响应脱敏——按域脱敏策略对响应体过滤(PII 形态),脱敏 MUST 在结果进入工具观察之前完成;(4) 重试分类——连接层未送达类失败 SHALL 重试至多一次,响应类错误(4xx/5xx/协议错误)MUST NOT 重试。策略器 SHALL 为纯函数形态(无外部状态依赖),可脱离容器做确定性单测(宪法 Article II)。

#### Scenario: 出口未命中白名单拒绝

- **GIVEN** 域白名单只含 api.example.com
- **WHEN** 域执行器试图出站 evil.example.com
- **THEN** 策略器出口校验拒绝,不发出网络请求,落审计

#### Scenario: 脱敏先于观察

- **GIVEN** 域响应体含邮箱形态字段
- **WHEN** 响应经策略器
- **THEN** 工具观察中的该字段已脱敏,Agent 面与 LLM 永远看不到原始值

#### Scenario: 响应错误不重试

- **GIVEN** 域端点返回 500
- **WHEN** 出站调用
- **THEN** 只调用一次,失败分类为响应类,不进入重试

#### Scenario: 缺策略器不注册

- **GIVEN** 域清单声明了域标识但无对应策略器实现
- **WHEN** 控制面启动
- **THEN** 该域不注册(与条件不满足同形的零感知语义),落启动告警

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

### Requirement: 域级审计与失败采集

每次域工具执行(成功/拒绝/失败/限流/超时)SHALL 落既有工具审计表,记录域标签、工具名、参数摘要、成败分类与耗时——与既有内置工具同表同语义。失败分类 SHALL 至少区分 unreachable(连接层)/protocol(响应类)/policy(策略器拒绝),降级为结构化错误观察喂回 LLM,Agent 面 MUST NOT 因域不可用崩溃、MUST NOT 编造结果;降级观察 MUST NOT 暴露内部堆栈或端点地址细节。非 LLM 调用 MUST NOT 落 LLM 用量记录。

#### Scenario: 执行留痕同表

- **GIVEN** 域工具执行一次(无论成败)
- **WHEN** 查询工具审计
- **THEN** 既有审计表新增一行且带域标签,无 sys_llm_usage 新行

#### Scenario: 域不可用降级

- **GIVEN** 某域端点服务未监听
- **WHEN** LLM 调用该域工具
- **THEN** 返回 unreachable 观察(含一次连接层重试),agent 据实说明域暂不可用,本轮问答不崩溃

### Requirement: 迁移与零回归

既有外部检索工具(knowledge_search)SHALL 收编为网关上的第一个域:其对外行为(注入/授权/观察形态/审计/重试语义)MUST 与迁移前逐字段一致,external-knowledge 主 spec 全部场景回归通过。既有内置工具(SQL 校验/执行/RAG 检索/公告/沙箱/动态 MCP)SHALL 行为零改动——网关分发仅新增域路由维度,未命中域的执行路径与引入前一致。

#### Scenario: knowledge_search 零回归

- **GIVEN** external-knowledge spec 的任一场景
- **WHEN** 迁移后执行
- **THEN** 行为与迁移前逐字段一致(观察形态/锚点/审计/失败分类)

#### Scenario: 内置工具零回归

- **GIVEN** 既有六个工具的任一调用
- **WHEN** 网关引入后执行
- **THEN** 执行路径与行为逐字段一致,评估基线不降

### Requirement: 域治理面

系统 SHALL 在既有工具管理面口径内提供域清单查询(域标识/端点形态/白名单摘要/启停状态/注册条件是否满足)与域启停操作,权限点沿用 system:tool:manage(仅 SUPER_ADMIN 种子覆盖)。域停用后,该域工具从注入集消失、回调执行被拒并落审计——与「条件不满足」同形。域清单查询 SHALL 不暴露凭证与完整白名单明细,只给摘要。

#### Scenario: 超管查询域清单

- **GIVEN** SUPER_ADMIN 已登录
- **WHEN** 访问域清单端点
- **THEN** 展示各域标识/启停/注册条件状态,不含凭证与白名单明细

#### Scenario: 停用域零感知

- **GIVEN** 超管停用某已注册域
- **WHEN** 之后任意调用方发起问答
- **THEN** 该域工具从注入集消失,直接回调被拒并落审计

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
