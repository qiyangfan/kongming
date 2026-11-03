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
SHALL 提供数据源切片与 prompt 版本切片;汇总 SHALL 另含答案评分观测:
已评分轮次的平均分与低分轮计数(低于既定低分阈值,阈值为部署常量)。
系统 SHALL 提供质量明细分页端点:按状态、数据源、时间范围筛选,每行含时间、
问题、改写问题、状态、重试次数、用户评分、trace_id,以及答案评分总分与
两维分(依据/覆盖;未评分轮为空,呈现为无评分而非 0 分)。明细行 SHALL 可
查看评分详情:维度分与完整主张分解(主张/重要度/支撑分级/覆盖标记),
供管理台审计。NO_DATA 轮次(无 SQL 执行、不落执行审计)SHALL 在汇总与
明细中可见——该形态今日在任何观测面不可见。汇总与明细端点 SHALL 以
`system:chat-quality:view` 权限点保护(仅 SUPER_ADMIN 种子覆盖),无权限返回
403。观测查询 MUST NOT 影响采集通道与主链路;观测聚合 SHALL 按租户维度收敛,
SHALL NOT 提供按用户行级切分(与「观测面数据边界」既定立约一致);评分数据
的可见性 SHALL 遵循同一收敛口径。

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

#### Scenario: 明细行携带答案评分

- GIVEN 窗口内部分轮次已评分、部分轮次未评分(降级或开关关闭期产生)
- WHEN 查询明细分页
- THEN 已评分行携带总分与依据/覆盖两维分,未评分行评分为空
- AND 空评分呈现为「未评分」形态,SHALL NOT 显示为 0 分

#### Scenario: 评分详情审计分解

- GIVEN 某轮已评分且分解含 critical 无支撑主张
- WHEN 管理台查看该轮评分详情
- THEN 可见维度分与完整主张分解(主张原文/重要度/支撑分级/覆盖标记),
  可定位扣分依据

#### Scenario: 汇总含评分观测

- GIVEN 窗口内已评分轮次的分数分布存在低于低分阈值的轮次
- WHEN 查询质量汇总
- THEN 汇总含已评分轮平均分与低分轮计数,评分观测遵循租户收敛口径

#### Scenario: 无权限不泄漏聚合

- GIVEN 查询方角色不含 system:chat-quality:view
- WHEN 访问质量汇总或明细端点
- THEN 返回 403,不返回任何聚合数据

### Requirement: 兜底轮分类观测

质量观测面 SHALL 区分兜底轮与真实知识未覆盖轮:汇总端点 SHALL 另含
兜底分类计数(闲聊/澄清引导两类,闲聊计数含复语直通与语义判定两个
来源);no_data 率口径 SHALL 剔除携带兜底分类标记的轮次(分子与分母
均不含,即兜底轮 SHALL NOT 计入未覆盖率),原始 NO_DATA 计数 SHALL
如实保留。明细分页行 SHALL 携带兜底分类字段并支持按其筛选(含
「无分类」档)。兜底分类观测 SHALL 遵循既有质量观测的租户收敛口径与
权限点保护;存量行(无分类标记)在筛选与计数中 SHALL 按「无分类」
参与,行为与引入前一致。

#### Scenario: 招呼轮不污染未覆盖率

- GIVEN 窗口内发生若干闲聊轮(直通与语义判定两来源)与若干真实
  NO_DATA 轮
- WHEN 查询质量汇总
- THEN 闲聊轮计入闲聊计数(两来源合并),不计入 no_data 率的分子与
  分母;真实 NO_DATA 轮计入未覆盖率,原始 NO_DATA 计数含全部
  NO_DATA 状态轮

#### Scenario: 明细按分类筛选定位

- GIVEN 明细分页行携带兜底分类字段
- WHEN 管理台按「闲聊」筛选
- THEN 仅返回 CHITCHAT 轮(两来源合并);按「无分类」筛选返回全部
  非兜底轮

#### Scenario: 存量数据零回归

- GIVEN 引入前产生的历史轮次(无分类标记)
- WHEN 查询汇总与明细
- THEN 历史轮按「无分类」参与计数与筛选,各既有指标口径不变

### Requirement: 节点阶段轨迹持久化

流式问答轮(recipe 链路)SHALL 在轮次终态落库时持久化该轮的节点阶段
轨迹:轨迹为有序数组,每条目含节点名、阶段状态(running/done)、自愈
重试序号与相对轮起点的耗时;条目数 SHALL 有上限保护(超出截断并带
截断标记)。轨迹来源 SHALL 为既有流式阶段事件(采集面零新增)。轨迹
SHALL 为管理面诊断字段,SHALL NOT 进入用户响应与流式终态事件。非流式
(同步)问答轮 SHALL NOT 产生轨迹(如实为空,SHALL NOT 事后虚构节点
顺序);传输失败轮 SHALL 以失败前已收集的部分如实落库。质量明细详情
SHALL 可查看该轮轨迹。轨迹查询 SHALL 遵循既有质量观测的租户收敛口径
与权限点保护。

#### Scenario: 流式轮落库有序阶段轨迹

- GIVEN 一次流式问答(触发 1 次 selfheal),persist=true
- WHEN 终态落库
- THEN 该消息携带有序轨迹:各节点 running/done 成对出现,自愈重试
  轮目带递增重试序号,条目含相对耗时
- AND 管理台该轮详情可见完整轨迹

#### Scenario: 同步轮不落轨迹

- GIVEN 一次非流式(同步)问答,persist=true
- WHEN 终态落库
- THEN 该消息轨迹为空,质量详情不展示轨迹块

#### Scenario: 传输失败轮如实落已收集部分

- GIVEN 流式传输中途失败,失败前已收集到前 4 个节点阶段事件
- WHEN 失败终态落库
- THEN 该消息轨迹包含且仅包含已收集的条目(如实,不补不删)

#### Scenario: 轨迹不进用户契约

- GIVEN 任一携带轨迹的消息
- WHEN 用户端响应与历史回显
- THEN 响应载荷不包含轨迹字段(与既有诊断字段出口纪律一致)

### Requirement: 同会话轮次串联

质量观测面 SHALL 提供同会话轮次串联查询:以明细行任一轮次为锚,返回
同会话全部轮次的轻量列表(时间升序、条数上限保护),每行含轮次标识、
问句、状态与可用的诊断标记(兜底分类/评分,按已合并变更为准)。质量
明细分页行 SHALL 携带会话标识。串联查询 SHALL 以既有质量观测权限点
保护并遵循同一租户收敛口径(audit 同款:data_scope=ALL 显式过滤,
其余收敛到绑定租户);越权锚点(不可见租户/不存在轮次)SHALL 返回
404,SHALL NOT 泄漏存在性。

#### Scenario: 明细锚定串联同会话轮次

- GIVEN 某会话含 5 个轮次,管理台明细定位到第 3 轮
- WHEN 发起串联查询
- THEN 返回该会话全部 5 轮的轻量列表,时间升序,含问句/状态/诊断标记

#### Scenario: 条数上限保护

- GIVEN 某会话轮次超过既定上限
- WHEN 发起串联查询
- THEN 返回最近上限数内的轮次并如实可辨(锚点轮必含)

#### Scenario: 越权锚点不泄漏

- GIVEN 查询方对锚点轮所属租户无可见性(data_scope 非 ALL 且未绑定)
- WHEN 发起串联查询
- THEN 返回 404,不返回任何轮次信息

#### Scenario: 无权限拒绝

- GIVEN 查询方角色不含质量观测权限点
- WHEN 发起串联查询
- THEN 返回 403

### Requirement: 轮次质量导出

系统 SHALL 提供质量明细分页同条件的 CSV 导出端点:列集对齐明细行
(含评分与兜底分类字段,按已合并变更为准),按当前筛选条件导出,
行数 SHALL 有上限保护(超出截断并在导出结果中如实可辨);CSV SHALL
遵循既有结果导出的转义纪律(UTF-8 BOM、RFC4180 引号转义、公式注入
中和)。导出端点 SHALL 以既有质量观测权限点保护并遵循同一租户收敛
口径;每次导出 SHALL 落操作日志(动作/筛选条件/行数)。导出 SHALL
为读路径快照,SHALL NOT 影响采集与主链路。

#### Scenario: 按筛选导出

- GIVEN 管理台已按状态与时间窗筛选
- WHEN 触发导出
- THEN 返回同筛选条件的 CSV,列集与明细行一致,编码与转义符合纪律

#### Scenario: 行数上限保护

- GIVEN 当前筛选命中行数超过上限
- WHEN 触发导出
- THEN 导出截断至上限,结果中可辨截断事实(如附截断标记行),SHALL NOT
  静默缺行

#### Scenario: 公式注入中和

- GIVEN 某轮问句含以公式起始符开头的文本(如「=HYPERLINK(...)」形态)
- WHEN 该行进入导出
- THEN 该字段经公式注入中和处理,表格软件打开不执行

#### Scenario: 导出权限与审计

- GIVEN 查询方角色不含质量观测权限点
- WHEN 触发导出
- THEN 返回 403;有权限导出时操作日志含动作、筛选条件与行数

### Requirement: 答案异步评分(分解与确定性重算)

成功的问答轮次(recipe 链路:数据/文档/融合,persist=true)SHALL 在终态
落库后**异步**触发答案质量评分,评分 SHALL NOT 阻塞或影响主链路响应。
职责分工 SHALL 固定:评审模型只产出**结构化主张分解**——事实主张清单
(主张=可对照证据验证的事实断言;纯主观评价、建议性收尾等非事实语句
SHALL NOT 纳入)、每条主张的重要度分级与支撑分级、按子问题要点拆解的
覆盖标记;分数 SHALL 由控制面按**确定性公式从分解重算**(0-100,依据分
按重要度加权、覆盖分按要点占比、两维加权后经校准映射),评审模型输出中
的任何分数字段 SHALL 被忽略;同一分解 SHALL 重算出同一分数。重要度最高级
(critical)的主张无支撑时,总分 SHALL 被上限封顶。评审输入问句 SHALL
优先使用指代消解后的自包含问题。评分 SHALL 为管理面诊断数据,SHALL NOT
进入用户响应与流式终态事件。

#### Scenario: 成功轮异步评分落库

- GIVEN 一次 SUCCESS 数据问答轮,persist=true,结果集非空
- WHEN 终态落库完成
- THEN 评分在后台异步执行,该消息获得恰好一条评分记录(总分+维度+分解)
- AND 评分执行期间与完成前后,本轮问答的响应内容与耗时不受任何影响

#### Scenario: 分数由分解确定性重算

- GIVEN 评审模型返回的主张分解(重要度/支撑分级/覆盖标记)保持不变
- WHEN 控制面重算分数(任意次)
- THEN 每次得到同一总分;评审模型输出中即使携带分数字段也被忽略

#### Scenario: critical 主张无支撑封顶

- GIVEN 某轮分解中存在 critical 主张且其支撑分级为无支撑
- WHEN 控制面重算分数
- THEN 总分不超过既定封顶上限,即使其余主张支撑良好

#### Scenario: 非目标轮次不评分

- GIVEN 一批轮次分别为 NO_DATA、REJECTED、FAILED、persist=false 临时对话、
  ReAct 链路轮
- WHEN 各轮终态处理
- THEN 全部不触发评分,不产生评分记录

### Requirement: 评分证据装配

评分证据装配口径:数据轮以该轮结果行列集为证据(受截断预算约束);
文档/融合轮以答案引用来源对应的文档摘录为证据(受每来源与来源数截断
预算约束)。两者均不可装配(无结果集且无有效来源引用)的轮次 SHALL NOT
评分。

#### Scenario: 数据轮以结果集为证据

- GIVEN 数据轮答案声称「华东区 3 月销售额为 X」,该轮结果集含对应数值
- WHEN 评分执行
- THEN 依据分以结果行列集为对照证据;主张数值与结果集一致计支撑,
  不一致计无支撑

#### Scenario: 文档轮以来源摘录为证据

- GIVEN 文档问答轮引用了 2 个文档来源
- WHEN 评分执行
- THEN 依据分以这两个来源对应的文档摘录为对照证据,答案主张超出
  摘录内容且无来源支撑的部分计无支撑

#### Scenario: 证据不可装配不评分

- GIVEN 某成功轮既无结果集也无有效来源引用
- WHEN 终态落库后的评分触发执行
- THEN 不产生评分记录,仅记日志,不影响该轮任何既有行为

### Requirement: 评分降级幂等与开关

评审不可达、超时或输出不可解析 SHALL NOT 落任何评分记录,仅记日志,
主链路与既有行为零感知;每条消息 SHALL 至多存在一条评分记录,重复触发
SHALL 幂等跳过。评分管线 SHALL 具部署级总开关:关闭时系统行为与本组
Requirement 引入前逐字一致。评审模型调用 SHALL 经控制面→Agent 面的内部
回调通道并受内部鉴权保护(ADR-002),控制面 SHALL NOT 直接持有模型凭证。

#### Scenario: 评审降级零感知

- GIVEN Agent 面评审模型不可达或输出不可解析
- WHEN 某成功轮触发评分
- THEN 不落评分记录、仅记日志;该轮响应与既有质量信号(status/retry/
  rating/trace_id)不受任何影响

#### Scenario: 重复触发幂等

- GIVEN 某消息已完成评分落库
- WHEN 同一消息再次触发评分(如进程重启后的重复投递)
- THEN 不产生第二条评分记录,既有记录不变

#### Scenario: 总开关关闭零回归

- GIVEN 部署配置关闭评分总开关
- WHEN 成功轮终态落库
- THEN 不发起任何评审调用,系统行为与本组 Requirement 引入前逐字一致

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
