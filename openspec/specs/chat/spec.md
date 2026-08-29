# chat 能力域

## Purpose

对话即产品主体验:会话/消息持久化让历史刷新不丢、跨设备可检索;查询结果可导出 CSV/Excel;EXPLAIN 执行计划可见(服务端构造、仅 SELECT、复用 data_scope 改写);图表支持多类型切换;多轮上下文由控制面按 sessionId 兜底补全。

## Requirements

### Requirement: 会话持久化(刷新不丢历史)

chat_session + chat_message 持久化对话历史,首次问答自动新建会话,刷新页面恢复历史,会话列表侧边栏可新建/切换/删除,按 user+tenant 隔离。

会话标题两阶段生成:初始标题=首问前 30 字符(截断兜底,保证任何时刻标题可用);首个成功问答轮(assistant 状态 SUCCESS)完成后,系统 SHALL 调用 LLM 将首问改写为简洁概括标题并回写会话。LLM 改写输出 SHALL 经净化(去首尾空白/换行/包裹引号)并截断至标题列上限以内;改写失败或超时 SHALL 保持截断标题,不阻断、不延迟问答主链路响应。标题已被用户手动重命名(不再等于截断初始值)的会话 SHALL 跳过自动改写,人工标题不被覆盖。

问答请求契约 SHALL 纯增 attachment_ids(图片附件引用,≤3 个,uuid 形态;缺省空=纯文本零回归,030);user 与 assistant 消息落库 SHALL 含附件引用摘要(filename/mime/size 三元组数组,不含内容),历史回显逐字同源;引用附件 MUST 先过归属校验(当前用户+租户,multimodal-input spec),任一无效 SHALL 404 同形且不进 agent 回调。

#### Scenario: 首次问答自动新建会话
- GIVEN 用户进入对话页(无当前会话)
- WHEN 发起首次提问
- THEN 后端自动新建会话(初始标题=首问前 30 字符),返回 sessionId
- AND user 问句与 assistant 结果都落消息表

#### Scenario: 首个成功轮后 LLM 改写标题
- GIVEN 新会话标题仍为截断初始值
- WHEN 该会话产生首个 assistant 状态 SUCCESS 的消息
- THEN 后端调用 LLM 以首问生成简洁标题并回写会话
- AND 改写期间问答响应照常返回(用户无感知)

#### Scenario: LLM 改写失败保持截断标题
- GIVEN LLM 服务不可达或返回异常
- WHEN 触发标题改写
- THEN 会话标题保持截断初始值,不报错、不重试风暴

#### Scenario: 手动重命名后不被自动改写覆盖
- GIVEN 用户已将会话重命名为自定义标题
- WHEN 后续问答轮成功完成
- THEN 标题保持用户设定值,不再触发自动改写

#### Scenario: 刷新页面恢复历史
- GIVEN 用户在会话中聊了多轮后刷新页面
- WHEN 重新进入对话页
- THEN 从本地存储读上次 sessionId,拉取历史消息完整回显

#### Scenario: 会话列表侧边栏
- GIVEN 用户有多个跨天会话
- WHEN 加载对话页
- THEN 侧边栏按更新时间倒序展示会话列表,并按「今天/昨天/近 7 天/近 30 天/更早」
  时间分组呈现,组内保持更新时间倒序,可新建/切换/删除

#### Scenario: 会话按 user+tenant 隔离
- GIVEN 用户 A 的会话
- WHEN 用户 B 尝试拉取该会话消息
- THEN 返回会话不存在或无权访问(不区分二者,不泄露信息)

#### Scenario: 带附件问答落库(030)

- GIVEN 用户引用 2 件已上传附件提问
- WHEN 问答完成
- THEN 消息含附件摘要;刷新回显徽标;attachment_ids 经归属校验

#### Scenario: 无附件零回归(030)

- GIVEN 纯文本问答(无 attachment_ids)
- WHEN 落库与回显
- THEN 消息形态与本字段引入前一致(空数组/缺省不渲染)

### Requirement: 结果导出 CSV/Excel

查询结果支持导出 CSV(UTF-8 with BOM,Excel 直接打开不乱码)与 Excel(xlsx,表头加粗+自动列宽),导出行数截断保护。导出端点 SHALL 复用与问答端点一致的鉴权约束:JWT 认证 + 权限点 `chat:execute` + X-Tenant-Id 租户白名单校验。导出 SHALL 以请求方已查询到的结果集(columns+rows)为输入,不重新执行 SQL——所见即所导,数据隔离在查询时已由 data_scope 保证,导出本身不产生新的数据访问。导出 SHALL 落操作日志 `chat.export`(与业务同事务;detail 含 fmt/rows/truncated;010 挂账⑦,015 清零)——数据外发可见性;审计写失败 SHALL NOT 阻断导出(告警日志,audit spec 铁律)。

#### Scenario: 导出 CSV
- GIVEN 查询结果有 columns+rows
- WHEN 点击「CSV」导出
- THEN 生成 UTF-8 with BOM 的 CSV,浏览器下载「查询结果-<时间戳>.csv」

#### Scenario: 导出 Excel
- GIVEN 查询结果有 columns+rows
- WHEN 点击「Excel」导出
- THEN 生成 xlsx(表头加粗+自动列宽),浏览器下载

#### Scenario: 导出不重跑 SQL
- GIVEN 某轮问答已返回结果集
- WHEN 对该结果集发起导出
- THEN 服务端仅做格式转换,不向业务库发起任何新查询
- AND 请求方无法借导出通道获取其未查询过的数据

#### Scenario: 导出行数上限保护
- GIVEN 结果集超出上限(默认 10000 行)
- WHEN 导出
- THEN 截断到上限,防止服务端内存失控

#### Scenario: 导出格式非法拒绝
- GIVEN 导出请求 format 非 csv/xlsx
- WHEN 提交
- THEN 返回参数错误,不生成文件

#### Scenario: 无权限导出 403
- GIVEN 已登录用户角色不含 chat:execute
- WHEN 请求导出端点
- THEN 返回 403

#### Scenario: 导出落操作日志
- GIVEN 用户执行导出
- WHEN 导出完成
- THEN sys_oper_log 含 chat.export 动作,detail 含 fmt/rows/truncated,与业务同事务
- AND 审计写失败不阻断导出(文件照常返回)

### Requirement: EXPLAIN 执行计划

提供 SELECT 语句的 EXPLAIN (FORMAT JSON) 执行计划查看;非 SELECT 语句被拒绝;EXPLAIN 复用 data_scope 改写防越权探测行。执行计划端点 SHALL 复用与问答端点一致的鉴权约束(JWT + `chat:execute` + 租户白名单),且 SHALL 以 assistant 消息标识为引用定位待分析 SQL——服务端从落库消息取 SQL,不接收客户端提交的任意 SQL 文本。执行计划 SHALL 由服务端统一构造 `EXPLAIN (FORMAT JSON)` 前缀、不使用 ANALYZE(计划语句不真正执行目标 SQL),并在该消息所属会话关联的数据源上执行;数据源不存在/禁用/越属时拒绝,语义与问答端点一致。被分析 SQL SHALL 完整经过与正常执行一致的防线(防火墙只读校验 → data_scope 改写 → 改写后复检),消息归属校验失败(非本人/跨租户/不存在)统一返回 404。

#### Scenario: 查看 SELECT 执行计划
- GIVEN 查询结果有 SQL
- WHEN 点击「执行计划」
- THEN 执行 EXPLAIN (FORMAT JSON),返回查询计划树
- AND 前端格式化展示(Node Type / cost / rows 缩进树)

#### Scenario: EXPLAIN 非 SELECT 被拦截
- GIVEN 尝试对 DELETE 等写语句做 EXPLAIN
- WHEN 经过 SQL 防火墙
- THEN 拒绝(原因:非只读语句),EXPLAIN 仅允许对 SELECT 分析

#### Scenario: EXPLAIN 复用 data_scope(防越权探测)
- GIVEN 用户对含 data_scope 约束的表做 EXPLAIN
- WHEN 执行
- THEN SQL 先经 data_scope 改写(与正常执行一致),防越权探测行

#### Scenario: 以消息引用定位 SQL,不收客户端裸 SQL
- GIVEN 客户端发起执行计划请求
- WHEN 请求仅携带消息标识
- THEN 服务端以该消息落库 SQL 为唯一分析对象,客户端无法借该通道提交任意语句
- AND 消息无落库 SQL(如纯失败轮)时返回友好参数错误

#### Scenario: 不使用 ANALYZE 真执行
- GIVEN 任意执行计划请求
- WHEN 服务端构造计划语句
- THEN 仅生成 EXPLAIN (FORMAT JSON) 形态,不含 ANALYZE,目标 SQL 不被真正执行

#### Scenario: 越权取他人消息执行计划 404
- GIVEN 消息属于用户 B 的会话
- WHEN 用户 A 请求该消息的执行计划
- THEN 返回 404,不区分「不存在」与「无权」

### Requirement: 图表可视化

图表区支持 bar/line/pie/scatter 四种类型切换,按列特征推断默认类型
(推断优先级:首列日期→line;存在两个数值列(任意位置)→scatter;
否则按首列基数走 pie/bar——低基数≤8 类→pie、≤50→bar),图表可下载 PNG。

手动切换为当前结果卡片内的本地状态:切换只改变系列形态,不改变图表
建议的 x/y 列映射(映射恒定);页面刷新或新消息进入后回落到后端建议
类型。scatter 渲染按 x 列全数值探测分流:x 列全部可数值化时渲染数值-
数值散点,否则退化为类目轴点图,不因非数值丢点致整图空白。

#### Scenario: 多类型图表切换
- GIVEN 查询返回可可视化的结果
- WHEN 结果展示
- THEN 图表区支持 bar/line/pie/scatter 四种类型切换
- AND 切换不改变 x/y 列映射,仅改变系列形态

#### Scenario: 切换状态回落
- GIVEN 用户已手动切换图表类型
- WHEN 页面刷新或同卡片新结果进入
- THEN 图表类型回落到后端建议类型

#### Scenario: 图表类型推断
推断优先级:首列日期 → line;否则存在两个数值列(任意位置)→ scatter;
否则按首列基数走 pie/bar。
- GIVEN 结果首列含日期
- WHEN 推断默认类型
- THEN 默认 line(时序趋势)
- GIVEN 结果存在两个数值列(任意位置,含标识列前置形态)
- THEN 默认 scatter(散点),x/y 取前两个数值列
- GIVEN 结果首列为低基数分类(≤8 类)且仅一个数值列
- THEN 默认 pie(占比)

#### Scenario: scatter 退化渲染
- GIVEN 用户手动切换到 scatter 且 x 列含非数值
- WHEN 渲染
- THEN 退化为类目轴点图,不因非数值丢点致整图空白

#### Scenario: 下载图表 PNG
- GIVEN 图表已渲染
- WHEN 点击下载
- THEN 生成 PNG(背景取当前主题卡片底色,非透明)并由浏览器下载
  「图表-<时间戳>.png」

### Requirement: 多轮上下文后端补全

显式 history 优先:请求携带非空 history 时直接使用;未携带或为空且
session_id 非空时,控制面 SHALL 从该会话消息表取最近 5 条 user 问句补全
history,传给 Agent 的 understand 节点做指代消解。临时对话 SHALL 由调用方
显式声明(persist=false),缺省行为是自动新建会话并落库——「不传 sessionId
即临时对话」与「首次问答自动新建会话」GIVEN 重叠,以显式声明为准消除歧义。

#### Scenario: sessionId 非空时后端补全 history
- GIVEN 前端传 sessionId 但未传 history
- WHEN 控制面处理查询
- THEN 从消息表取最近 5 条 user 问句作 history,传给 Agent 指代消解

#### Scenario: 显式 history 优先于后端补全
- GIVEN 请求同时携带 sessionId 与非空 history
- WHEN 控制面处理查询
- THEN 使用请求携带的 history,不做后端补全

#### Scenario: 不传 sessionId 的临时对话兼容
- GIVEN 调用方不传 sessionId
- WHEN 查询
- THEN 行为与常规查询一致:缺省自动新建会话并落库(与「首次问答自动新建
  会话」同一行为);仅当显式声明 persist=false 时才不新建会话、不落消息
  (临时对话),history 仍可由调用方显式传

### Requirement: 问答查询端点

系统 SHALL 提供用户面问答端点:JWT 认证 + 权限点 `chat:execute` 校验 +
X-Tenant-Id 租户白名单校验(缺省取用户默认租户,未绑定拒绝,SUPER_ADMIN
不豁免)。请求 SHALL 携带 question;SHOULD 携带 session_id(缺省自动新建会话,
标题规则见「会话持久化」requirement 的两阶段生成)、persist(缺省 true)、
history(显式多轮上下文)、datasource_key(缺省 default)。系统 SHALL 为每次问答
生成 trace_id 并贯穿 Agent 面与执行审计。响应 SHALL 携带 session_id、success、
verdict、summary、insights、stats、columns、rows、sql、chart(图表配置建议)、
retry_count;失败时 SHALL 携带友好 error,不暴露内部堆栈。响应 SHALL 纯增携带
message_id(assistant 落库消息 id,供反馈提交引用;persist=false 或无会话调用
为 null)与 datasource_key(本次执行的数据源快照)——纯增字段,老调用方不受
影响(feedback spec「messageId 打通」)。消息落库 status 三态映射:
verdict=PASS → SUCCESS;verdict=BLOCKED → REJECTED;verdict=ERROR 或重试
耗尽 → FAILED。Agent 面不可达/超时时 SHALL 返回友好错误并落 FAILED 消息
(persist=true 时),不裸 500。

#### Scenario: 有权限用户提问成功
- GIVEN 已登录用户角色含 chat:execute,租户 default 已绑定
- WHEN 提交问题(无 session_id)
- THEN 自动新建会话并返回 session_id,user 问句与 assistant 结果各落一条消息
- AND 响应含 success=true、verdict=PASS、summary/columns/rows/sql/chart
- AND 响应含 message_id(assistant 消息 id)与 datasource_key
- AND 执行审计的 trace_id 与本次问答 trace_id 一致

#### Scenario: 无 chat:execute 权限拒绝
- GIVEN 已登录用户角色不含 chat:execute
- WHEN 提交问题
- THEN 返回 403,不新建会话、不落消息、不回调 Agent 面

#### Scenario: 未认证拒绝
- GIVEN 请求无有效 JWT
- WHEN 提交问题
- THEN 返回 401

#### Scenario: 未绑定租户拒绝
- GIVEN 请求 X-Tenant-Id 指向用户未绑定的租户
- WHEN 提交问题
- THEN 返回 403,不回调 Agent 面

#### Scenario: 权限类拦截落 REJECTED
- GIVEN 问答链路最终 verdict=BLOCKED(生成 SQL 被防火墙拦截且按契约不重试)
- WHEN 响应返回
- THEN success=false,assistant 消息 status=REJECTED,响应含友好提示

#### Scenario: Agent 面不可达
- GIVEN Agent 面进程宕机或调用超时
- WHEN 提交问题
- THEN 返回友好错误(不暴露内部地址/堆栈),assistant 消息 status=FAILED
- AND 不向上抛裸 500

#### Scenario: 图表配置建议推断
- GIVEN 查询结果返回
- WHEN 生成响应
- THEN 按列特征给出 chart 建议:首列日期/时间类型→line;低基数分类(≤8 类)
  +数值→pie;类别列+数值列→bar;明细或无数值列→none

### Requirement: 会话列表与消息历史端点

系统 SHALL 提供会话列表端点:JWT 认证 + 权限点 `chat:execute` 校验 +
租户白名单校验(X-Tenant-Id,缺省取用户默认租户,语义与问答端点一致);
返回当前用户在当前租户下的会话(id/标题/更新时间),按更新时间倒序,
SHOULD 支持条数上限参数(缺省 50)。列表 SHALL 只含本人本租户会话,
不含他人或他租户会话(过滤而非报错)。

系统 SHALL 提供会话消息端点(路径含 session_id):同一鉴权与租户约束;
返回该会话全部消息按创建时间升序,user 消息含问句,assistant 消息
SHALL 携带结构化应答要素(状态/摘要/SQL/结果列/行/洞察/统计/图表建议),
要素缺失时以缺省形态呈现(如无 SQL 则空);assistant 消息 SHALL 纯增携带
rating(null=未反馈)、corrected_sql(null=无纠错)与 feedback_comment
(null=无留言)反馈状态字段供回显(feedback spec「messageId 打通」;
comment 为点踩弹窗预填源,R1-S3),user 消息不携带。归属校验:会话不存在、
非本人、非当前租户三种情形 SHALL 统一返回 404,不区分语义
(不泄露会话存在性)。

系统 SHALL 提供会话删除端点(路径含 session_id):同一鉴权与租户约束;
删除会话并级联删除其全部消息;归属校验语义同上(404)。删除幂等语义:
重复删除同一会话返回 404(第二次已不存在)。

#### Scenario: 列表只含本人本租户会话
- GIVEN 用户 A 在租户 T1 有会话 S1,用户 B 在 T1 有 S2,A 在 T2 有 S3
- WHEN 用户 A 以租户 T1 请求会话列表
- THEN 返回含 S1 不含 S2/S3

#### Scenario: 列表按更新时间倒序
- GIVEN 用户有会话 S1(3 天前更新)与 S2(刚刚更新)
- WHEN 请求会话列表
- THEN S2 排在 S1 前

#### Scenario: 越权取他人会话消息统一 404
- GIVEN 会话 S 属于用户 B
- WHEN 用户 A 请求 S 的消息
- THEN 返回 404,响应不区分「不存在」与「无权」

#### Scenario: 跨租户取会话 404
- GIVEN 会话 S 属于用户 A 但租户为 T2
- WHEN 用户 A 以租户 T1 请求 S 的消息
- THEN 返回 404

#### Scenario: 消息回显保序且要素完整
- GIVEN 会话含 3 轮问答(其中 1 轮被拦截)
- WHEN 请求该会话消息
- THEN 按创建时间升序返回;被拦截轮 assistant 消息 status=REJECTED
  且携带拦截文案,成功轮携带摘要/SQL/结果/图表建议
- AND 每条 assistant 消息含 rating/corrected_sql/feedback_comment 反馈状态字段

#### Scenario: 删除会话级联清消息
- GIVEN 会话 S 含多轮消息
- WHEN 持有者删除 S
- THEN S 从列表消失,再请求 S 的消息返回 404

#### Scenario: 无权限访问列表 403
- GIVEN 已登录用户角色不含 chat:execute
- WHEN 请求会话列表/消息/删除任一端点
- THEN 返回 403

#### Scenario: 未认证访问 401
- GIVEN 请求无有效 JWT
- WHEN 请求任一新端点
- THEN 返回 401

### Requirement: 会话重命名

系统 SHALL 提供会话重命名端点(路径含 session_id):JWT 认证 + 权限点
`chat:execute` + 租户白名单校验(与问答端点一致)。标题 SHALL 去首尾空白后
非空且不超标题列上限(200),非法标题返回参数错误且不更新;归属校验失败
(非本人/跨租户/不存在)统一返回 404。重命名成功 SHALL 更新标题并刷新会话
更新时间;重命名后的会话标题不再被 LLM 自动改写覆盖(见「会话持久化」)。

#### Scenario: 重命名成功
- GIVEN 用户持有会话 S
- WHEN 提交新标题「月度复盘」
- THEN S 标题更新为「月度复盘」,会话列表按新更新时间排序

#### Scenario: 空白标题拒绝
- GIVEN 新标题去空白后为空串
- WHEN 提交重命名
- THEN 返回参数错误,原标题不变

#### Scenario: 越权重命名他人会话 404
- GIVEN 会话 S 属于用户 B
- WHEN 用户 A 请求重命名 S
- THEN 返回 404,不区分「不存在」与「无权」

#### Scenario: 无权限重命名 403
- GIVEN 已登录用户角色不含 chat:execute
- WHEN 请求重命名端点
- THEN 返回 403

### Requirement: 会话搜索

会话列表端点 SHALL 支持可选搜索词参数:按会话标题或会话内用户问句内容
匹配(大小写不敏感;LIKE 通配符 %/_/\ SHALL 按字面转义,不展开为模式)。
不携带搜索词时列表行为与现状完全一致(向后兼容)。搜索结果 SHALL 仍只含
本人本租户会话,按更新时间倒序,受列表条数上限收口。

#### Scenario: 按标题命中
- GIVEN 用户有会话标题含「销售复盘」
- WHEN 以搜索词「复盘」请求会话列表
- THEN 返回该会话

#### Scenario: 按问句内容命中
- GIVEN 会话某轮 user 问句含「连弩」,标题不含
- WHEN 以搜索词「连弩」请求会话列表
- THEN 返回该会话

#### Scenario: 通配符按字面匹配
- GIVEN 会话标题含字面「100%达成」
- WHEN 以搜索词「100%」请求
- THEN 命中该标题
- AND 搜索词「_」不命中所有会话(按字面值匹配)

#### Scenario: 搜索不破坏隔离
- GIVEN 用户 B 的会话问句含「连弩」
- WHEN 用户 A 以搜索词「连弩」请求(同租户)
- THEN 不返回 B 的会话

#### Scenario: 空搜索词等价不搜索
- GIVEN 用户有多个会话
- WHEN 搜索词缺省或为纯空白
- THEN 返回与不携带搜索词一致的完整列表(倒序+上限收口)


### Requirement: 流式问答端点

系统 SHALL 提供用户面流式问答端点 `POST /chat/query/stream`:鉴权与
前段防线(权限点 `chat:execute` + X-Tenant-Id 租户白名单 + 数据源入口
预检 + 会话处理 + history 补全 + USER 消息落库)SHALL 与同步问答端点
共用同一编排;响应形态为 SSE(text/event-stream,Cache-Control no-cache),
事件契约见 query-streaming spec。Agent 面流 SHALL 由控制面中继:阶段/
sql/summary 事件透传,result/error 终态由控制面在落库后构造下发
(result 事件在 assistant 消息落库之后发出,message_id 即落库消息 id)。
前端断开后控制面 SHALL 继续消费 Agent 面流至终态并照常落库(断线续存,
落库走独立数据库会话)。流式响应 SHALL 周期发送 SSE 注释行心跳
(缺省 15s)。传输层失败(Agent 面不可达/坏报文)SHALL 收敛 error 终态
事件+FAILED 消息落库(persist=true 时),不裸 500。首个成功轮的标题
自动改写触发条件 SHALL 与同步端点一致。

#### Scenario: 流式问答全链
- GIVEN 已登录用户角色含 chat:execute 且租户已绑定
- WHEN 以流式端点提交问题
- THEN 响应为 SSE 流:阶段事件逐个到达,校验通过后 sql 事件先于终态
- AND result 事件 data 与同步端点响应同构(含 message_id)
- AND user 与 assistant 消息照常落库

#### Scenario: 流开始前校验失败
- GIVEN 会话不存在或数据源非法或租户未绑定
- WHEN 以流式端点提交问题
- THEN 返回常规 JSON 错误响应(404/400/403,与同步端点同码同 detail)

#### Scenario: 断线续存落库
- GIVEN 流式问答进行中前端断开
- WHEN Agent 面流继续
- THEN 控制面消费至终态,assistant 消息/回写照常落库(独立会话)
- AND 重进会话历史回显完整结果

#### Scenario: Agent 面流失败收敛
- GIVEN Agent 面不可达或返回非 200 或报文不可解析
- WHEN 流式问答进行
- THEN 下发 error 终态事件(友好文案,不暴露内部地址)并落 FAILED 消息

### Requirement: Agent 模式选择(问答端点)

问答查询端点(同步与流式)的请求 SHALL 纯增可选字段 agent_mode ∈ {recipe, react},缺省 recipe(既有 8 节点链路,零回归);agent_mode=react 时控制面 SHALL 改调 Agent 面 ReAct 内核端点,并在请求中按调用方角色集注入授权工具 schema 子集。调用方无任何工具权限点时 SHALL 注入空工具集(ReAct 退化为纯文本问答,不报错)。响应结构与既有问答端点逐字段同构;trace_id 生成与审计贯穿语义不变。

#### Scenario: 缺省 recipe 零回归

- GIVEN 问答请求不携带 agent_mode
- WHEN 提交问题
- THEN 走既有 8 节点链路,响应与行为与 019 之前一致

#### Scenario: react 模式走 ReAct 内核

- GIVEN 已登录用户含工具权限点,请求 agent_mode=react
- WHEN 提交问题
- THEN 控制面调用 Agent 面 ReAct 端点并注入该用户授权的工具 schema 集,响应字段与 recipe 同构

#### Scenario: 无工具权限的 react 退化

- GIVEN 已登录用户角色不含任何工具权限点,请求 agent_mode=react
- WHEN 提交问题
- THEN 注入空工具集,LLM 无工具可用,退化为纯文本回答(成功终态,不报错)

#### Scenario: 非法 agent_mode 值拒绝

- GIVEN 请求 agent_mode 为枚举外取值
- WHEN 提交问题
- THEN 传输层校验拒绝(422),不回调 Agent 面

### Requirement: 写工具确认端点

系统 SHALL 提供写工具确认端点:JWT 认证 + 归属校验(仅待确认调用所属会话的发起人可确认/拒绝,他人操作拒绝且不暴露存在性,并落拒绝审计)。确认请求携带确认标识与决策(confirm/reject);确认时控制面 SHALL 按待确认记录重放该工具执行,防御全量复校(用户-租户当前绑定/工具权限点/参数 JSON Schema 三项,适用人工确认路径;限流不适用),执行结果落审计并以 assistant 消息形式落执行回执(persist 语义与所属会话一致);拒绝时落审计、不执行、回执消息说明已取消。待确认记录 SHALL 有过期时间,过期确认按拒绝语义处理并说明。同一确认标识重复提交 SHALL 幂等(返回首次决策结果含执行结果,不重复执行)。

#### Scenario: 确认后执行并回执

- GIVEN 当前用户会话内存在未过期待确认调用
- WHEN 提交 confirm 决策
- THEN 工具被执行,落 EXECUTED 审计,会话内新增 assistant 回执消息(含执行结果摘要)

#### Scenario: 拒绝执行

- GIVEN 当前用户会话内存在未过期待确认调用
- WHEN 提交 reject 决策
- THEN 工具不执行,落审计,回执消息说明该操作已取消

#### Scenario: 他人确认拒绝

- GIVEN 待确认调用属于用户 A 的会话
- WHEN 用户 B 携带该确认标识提交确认
- THEN 拒绝(404 同形,不暴露存在性),不执行

#### Scenario: 过期确认

- GIVEN 待确认记录已过期
- WHEN 提交 confirm 决策
- THEN 按拒绝语义处理(不执行),回执说明已过期

### Requirement: 问答链路输入输出护栏挂接

问答查询端点(同步与流式)的用户输入 SHALL 在回调 Agent 面前过输入护栏(prompt-guardrails spec):拦截时落 REJECTED 消息(安全检查未通过文案,复用既有 REJECTED 分态)并返回 success=false,不回调 Agent 面;脱敏时脱敏值进链路(USER 消息落库与审计记原始问句的口径不变——审计 sys_sql_audit 不受影响,护栏事件落 sys_guardrail_audit)。Agent 面返回的自然语言答案/摘要 SHALL 在落库与返回前过输出护栏;输出护栏拦截时消息 status=SUCCESS(查询本身成功),摘要替换为安全文案。护栏故障 SHALL fail-closed(输入按拦截、输出按替换),不裸 500。

#### Scenario: 输入拦截落 REJECTED

- GIVEN 用户问句命中 injection 规则
- WHEN 提交问答
- THEN 响应 success=false、assistant 消息 status=REJECTED、文案为安全检查未通过
- AND 不回调 Agent 面,sys_sql_audit 无记录,sys_guardrail_audit 落拦截记录

#### Scenario: 输入脱敏正常问答

- GIVEN 用户问句含手机号
- WHEN 提交问答
- THEN 问答正常进行(Agent 收到脱敏问句),sys_guardrail_audit 落脱敏记录

#### Scenario: 输出泄漏拦截

- GIVEN Agent 返回的摘要含系统提示词特征片段
- WHEN 响应组装
- THEN 摘要替换为安全文案,消息 SUCCESS(结果集照常),sys_guardrail_audit 落拦截记录

#### Scenario: 护栏关闭项零行为变化

- GIVEN 全部护栏规则被禁用(或无任何命中)
- WHEN 提交问答
- THEN 链路行为与护栏引入前逐字段一致(零回归)

### Requirement: 记忆召回与显式记忆挂接

问答查询端点的链路 SHALL 在回调 Agent 面前执行记忆召回(当前问句向量 top-3,user+tenant 双过滤),召回结果经 memory_hint 传给 Agent 面;召回服务失败 SHALL 降级无注入(问答主链路不受影响)。用户输入含「记住/请记住」意图词时 SHALL 走显式记忆形成路径(agent-memory spec):落库后答复确认,不再走数据问答链路。响应契约零变化(纯链路内增强)。

#### Scenario: 召回注入

- GIVEN 用户有非 dormant 记忆
- WHEN 提交问答
- THEN Agent 请求体含 memory_hint,问答正常进行

#### Scenario: 召回失败降级

- GIVEN 记忆召回服务异常(embedding 不可达等)
- WHEN 提交问答
- THEN 按无注入处理,问答正常完成,失败仅日志

#### Scenario: 显式记忆短路

- GIVEN 用户输入「请记住:我偏好月度口径」
- WHEN 提交问答
- THEN 落显式记忆,答复「已记住」,不产生 SQL 执行

### Requirement: 消息 artifacts 字段

问答响应与历史回显的消息模型 SHALL 纯增 artifacts 数组(conversation-artifacts spec 的派生语义);同步/流式/回显三路径同源派生,字段形态逐字一致;老消息(无 artifacts 档)回显时 SHALL 按要素确定性重算兜底(与 chart 重算同款先例),显式空档(如 agent 不可达 FAILED 轮)按档原样回显。

#### Scenario: 三路径同源

- GIVEN 同一轮问答经同步响应/流式 result/历史回显
- WHEN 读取
- THEN 三处 artifacts 内容一致

#### Scenario: 老消息缺档重算

- GIVEN 026 之前落库的消息(result_json 无 artifacts 键)
- WHEN 回显
- THEN 按 sql/chart/summary 要素确定性重算 artifacts,与新消息同形态渲染

#### Scenario: 显式空档不重算

- GIVEN agent 不可达的 FAILED 消息(result_json 显式 artifacts=[])
- WHEN 回显
- THEN artifacts 为空数组,不派生 document 卡(与当轮同步响应一致)

### Requirement: 消息状态第四态(NO_DATA)

chat_message.status SHALL 支持第四态 `NO_DATA`:agent verdict=NO_DATA
的回答以该状态持久化——`summary` 承载中性说明文案、`error` 为空、产物
卡为空。呈现 SHALL 为中性说明形态(非错误横幅、非成功结果卡),反馈
操作 SHALL 照常可用;web 实时映射(verdict→status)与历史回显 SHALL
与该语义一致。既有三态(SUCCESS/FAILED/REJECTED)的映射与呈现 SHALL
逐字不变。

#### Scenario: NO_DATA 消息持久化与中性呈现

- GIVEN agent 返回 verdict=NO_DATA、error=「当前数据源未找到回答该问题所需的数据…」
- WHEN chat 终态组装与落库
- THEN 消息 status=NO_DATA、summary=说明文案、result_json.error 为空、产物卡为空
- AND 响应与历史回显以中性说明形态渲染(非错误横幅),反馈区可用

#### Scenario: 既有三态零回归

- GIVEN PASS/BLOCKED/ERROR 三类既有终态
- WHEN 终态组装与落库
- THEN 映射与呈现行为与引入前逐字一致
