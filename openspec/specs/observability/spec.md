# observability 能力域

## Purpose

LLM 用量的可观测:采集补全(失败调用与延迟也留痕)、聚合查询(汇总/趋势/明细,
含失败与延迟统计)、trace 时间线(一次请求的 LLM 调用+工具执行+SQL 执行三路
事件流)。采集通道与字段口径归 multi-model-routing 域「用量采集与统计」;
本域管"看得见":聚合端点行为、时间线拼装、观测面边界。授权模型归 rbac 域。

## Requirements

### Requirement: 用量汇总与趋势查询

系统 SHALL 提供用量汇总端点:按租户+模型+任务分组,返回调用次数、成功/失败
次数、prompt/completion/total tokens 合计、latency_ms 平均值与最大值,支持时间
范围筛选;权限点 system:llm-usage:view(仅 SUPER_ADMIN 种子覆盖)。系统 SHALL
提供按天趋势端点:指定天数窗口(上限 90)内按日历日(Asia/Shanghai 口径)分桶
逐天补零返回 tokens 与调用次数,时间边界换算 MUST 统一 Asia/Shanghai
(月聚合时区教训先例)。

#### Scenario: 汇总含失败与延迟统计

- GIVEN 库内某窗口含成功与失败两类用量记录
- WHEN 按该窗口查询汇总
- THEN 每分组返回 calls、calls_error、tokens 三项合计、latency_ms_avg 与
  latency_ms_max(ERROR 记录 tokens=0 计入 calls/calls_error,不计入延迟统计)

#### Scenario: 趋势逐天补零

- GIVEN 窗口内某天无任何用量记录
- WHEN 查询按天趋势
- THEN 该天仍返回一桶(tokens=0,calls=0),桶数与窗口天数一致

#### Scenario: 无权限拒绝

- GIVEN 登录用户无 system:llm-usage:view
- WHEN 调用汇总/趋势/明细/时间线任一端点
- THEN 返回 403,并落权限拒绝审计

### Requirement: 用量明细分页查询

系统 SHALL 提供用量明细分页端点(按 created_at 倒序),支持模型、任务类型、
成败状态、trace_id 精确、租户五类筛选(租户按 tenant_id 直滤——该租户全部
行均返回,含 tenant_key 反解失败展示为 unknown 的行);每行返回时间、模型、
任务、status、tokens、latency_ms、trace_id、租户(可空,反解失败归 unknown)。

#### Scenario: 筛选与分页

- GIVEN 库内多条用量记录
- WHEN 按 model+status 筛选并翻页
- THEN 仅返回匹配行,倒序分页,total 与页内容一致

#### Scenario: 按 trace_id 定位

- GIVEN 一次问答产生的一组用量记录
- WHEN 以该 trace_id 精确筛选
- THEN 仅返回该次请求的全部 LLM 调用记录

#### Scenario: 按租户筛选

- GIVEN 租户 T1 与 T2 各有用量记录,且 T1 存在一行 tenant_key 反解失败(展示为 unknown)
- WHEN 以 T1 的 tenant_id 筛选
- THEN 返回 T1 的全部记录(含该 unknown 展示行),T2 记录不返回

### Requirement: trace 时间线查询

系统 SHALL 提供 trace 时间线端点:按 trace_id 精确查询,聚合该 trace 下
LLM 调用量(sys_llm_usage:模型/任务/status/tokens/latency/时间)、工具执行
(sys_tool_audit:工具名/status/duration/时间)、SQL 执行(sys_sql_audit:
verdict/时间)三类事件,按发生时间升序拼装为单一事件流返回。trace_id 不存在
时返回空事件流(不 404,与审计查询惯例一致)。**时间线为只读聚合视图,零新
采集**——三类事件的数据由既有采集管道落库。

#### Scenario: 一次问答的全链路事件流

- GIVEN 一次 ReAct 问答(多轮 LLM 调用+工具执行+SQL 执行,同一 trace_id)
- WHEN 查询该 trace_id 的时间线
- THEN 返回全部事件按时间升序排列,每事件含类别(LLM/TOOL/SQL)、名称、
  成败、耗时与时间戳

#### Scenario: 空事件流

- GIVEN 传入的 trace_id 从未产生任何事件
- WHEN 查询时间线
- THEN 返回空事件流(HTTP 200)

### Requirement: 观测面数据边界

观测端点为管理面:数据按租户维度聚合展示(无行级按用户切分——sys_llm_usage
无用户维度,立约为既定边界);用量记录不含 prompt/response 内容(采集面不落
原文,天然免脱敏)。观测查询 MUST NOT 影响采集通道:聚合/明细/时间线端点的
任何故障不波及主链路与上报接收端点。

#### Scenario: 观测查询与采集隔离

- GIVEN 用量上报通道正常工作
- WHEN 聚合端点因任意原因失败
- THEN 上报接收与主链路行为不受影响

#### Scenario: 租户维度聚合

- GIVEN 多租户用量记录(tenant 反解失败者归 unknown)
- WHEN 查询汇总
- THEN 分组按租户呈现,unknown 单列,不跨租户混算

### Requirement: 轮次质量观测

系统 SHALL 提供问答轮次的质量观测面。轮次终态落库(persist=true)时 SHALL 记录
四个质量信号:trace_id(与 LLM 用量/SQL 执行审计的关联键)、selfheal 重试次数、
指代消解后的改写问题、prompt 版本指纹;其中改写问题 SHALL 仅在多轮指代消解
实际发生(改写结果与原问题不同)时非空。四信号为管理面诊断字段,SHALL NOT 进入
用户响应与流式终态事件。

系统 SHALL 提供质量汇总端点:按时间范围(上限 90 天)返回轮次四态
(SUCCESS/REJECTED/NO_DATA/FAILED)占比、selfheal 率、点踩率与平均 token,
SHALL 提供按天趋势(Asia/Shanghai 日历日,逐日补零,口径与用量趋势一致),
SHALL 提供数据源切片与 prompt 版本切片。系统 SHALL 提供质量明细分页端点:
按状态、数据源、时间范围筛选,每行含时间、问题、改写问题、状态、重试次数、
用户评分、trace_id。NO_DATA 轮次(无 SQL 执行、不落执行审计)SHALL 在汇总与
明细中可见——该形态今日在任何观测面不可见。汇总与明细端点 SHALL 以
`system:chat-quality:view` 权限点保护(仅 SUPER_ADMIN 种子覆盖),无权限返回
403。观测查询 MUST NOT 影响采集通道与主链路;观测聚合 SHALL 按租户维度收敛,
SHALL NOT 提供按用户行级切分(与「观测面数据边界」既定立约一致)。

#### Scenario: 多轮轮次落库改写问题与重试次数

- GIVEN 一次多轮问答(指代消解发生且 selfheal 触发 1 次),persist=true
- WHEN 轮次终态落库
- THEN assistant 消息含 trace_id、retry_count=1、resolved_question 为改写后的自包含问题
- AND 该轮次在质量明细分页中可见

#### Scenario: 单轮不写改写问题

- GIVEN 一次无历史上下文的单轮问答,persist=true
- WHEN 轮次终态落库
- THEN resolved_question 为空(无改写发生),retry_count 如实记录(无重试为 0)

#### Scenario: NO_DATA 轮次可观

- GIVEN 某时间窗口内发生过知识未覆盖(NO_DATA)轮次——此类轮次无 SQL 执行、不落执行审计
- WHEN 查询质量汇总或明细分页
- THEN NO_DATA 计入四态占比与趋势,明细可定位到具体轮次

#### Scenario: 趋势按天补零

- GIVEN 查询窗口内某天无任何问答
- WHEN 查询按天趋势
- THEN 该天返回零值桶,桶数与窗口天数一致,日期口径为 Asia/Shanghai

#### Scenario: 数据源切片定位质量洼地

- GIVEN 同一时间窗内多个数据源各有轮次
- WHEN 查询质量汇总
- THEN 按数据源返回四态切分行,可定位未覆盖率高的数据源

#### Scenario: 无权限不泄漏聚合

- GIVEN 查询方角色不含 system:chat-quality:view
- WHEN 访问质量汇总或明细端点
- THEN 返回 403,不返回任何聚合数据

### Requirement: 用量协议归因

LLM 用量记录 SHALL 落 `protocol` 列,取值域与多模型注册表出站协议单源一致
(`openai` | `anthropic` | `responses`;ADR-008,不新造枚举)。每次调用(含失败
尝试)SHALL 各带其出站协议;未携带(旧版本 agent 上报)SHALL 落 null 并正常受理。
明细分页与汇总 SHALL 支持按 protocol 过滤(过滤型,不新增分组维度);不过滤时
计数 SHALL 与引入前逐字一致。明细返回行 SHALL 含 protocol 列(null 展示口径
为「-」,不回算、不推断——model 名不可可靠映射协议)。protocol 为归因维度,
SHALL NOT 接入成本估算轨(075 三条成本口径纪律;归因不等于计价)。

#### Scenario: 三通道用量可分

- GIVEN 注册表两条目(model 同名、protocol 分别为 openai 与 responses)各发生调用
- WHEN 查询用量明细并按 protocol 筛选
- THEN 两股流量分别返回,同名 model 不再混为一行

#### Scenario: 失败尝试同样归属协议

- GIVEN 某 anthropic 条目的调用以 ERROR 终态
- WHEN 查询该协议用量
- THEN ERROR 行可见且 protocol=anthropic(失败率与额外成本可见)

#### Scenario: 存量行 null 不透出

- GIVEN 升级前的历史用量行(protocol 为 null)
- WHEN 查询用量明细
- THEN 该行正常返回,protocol 列展示「-」,SHALL NOT 回算或推断
