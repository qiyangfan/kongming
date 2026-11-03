# Spec Delta

## ADDED Requirements

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

## MODIFIED Requirements

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
