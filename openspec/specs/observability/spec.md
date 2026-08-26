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
