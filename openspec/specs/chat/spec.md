# chat 能力域

## Purpose

对话即产品主体验:会话/消息持久化让历史刷新不丢、跨设备可检索;查询结果可导出 CSV/Excel;EXPLAIN 执行计划经防火墙白名单放行后可见;图表支持多类型切换;多轮上下文由控制面按 sessionId 兜底补全。

## Requirements

### Requirement: 会话持久化(刷新不丢历史)

chat_session + chat_message 持久化对话历史,首次问答自动新建会话,刷新页面恢复历史,会话列表侧边栏可新建/切换/删除,按 user+tenant 隔离。

#### Scenario: 首次问答自动新建会话
- GIVEN 用户进入对话页(无当前会话)
- WHEN 发起首次提问
- THEN 后端自动新建会话(标题=首问前 30 字符),返回 sessionId
- AND user 问句与 assistant 结果都落消息表

#### Scenario: 刷新页面恢复历史
- GIVEN 用户在会话中聊了多轮后刷新页面
- WHEN 重新进入对话页
- THEN 从本地存储读上次 sessionId,拉取历史消息完整回显

#### Scenario: 会话列表侧边栏
- GIVEN 用户有多个会话
- WHEN 加载对话页
- THEN 侧边栏显示会话列表(按 updated_at 倒序),可新建/切换/删除

#### Scenario: 会话按 user+tenant 隔离
- GIVEN 用户 A 的会话
- WHEN 用户 B 尝试拉取该会话消息
- THEN 返回会话不存在或无权访问(不区分二者,不泄露信息)

### Requirement: 结果导出 CSV/Excel

查询结果支持导出 CSV(UTF-8 with BOM,Excel 直接打开不乱码)与 Excel(xlsx,表头加粗+自动列宽),导出行数截断保护。

#### Scenario: 导出 CSV
- GIVEN 查询结果有 columns+rows
- WHEN 点击「CSV」导出
- THEN 生成 UTF-8 with BOM 的 CSV,浏览器下载「查询结果-<时间戳>.csv」

#### Scenario: 导出 Excel
- GIVEN 查询结果有 columns+rows
- WHEN 点击「Excel」导出
- THEN 生成 xlsx(表头加粗+自动列宽),浏览器下载

#### Scenario: 导出行数上限保护
- GIVEN 结果集超出上限(默认 10000 行)
- WHEN 导出
- THEN 截断到上限,防止服务端内存失控

### Requirement: EXPLAIN 执行计划

提供 SELECT 语句的 EXPLAIN (FORMAT JSON) 执行计划查看;非 SELECT 语句被拒绝;EXPLAIN 复用 data_scope 改写防越权探测行。

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

### Requirement: 图表可视化

图表区支持 bar/line/pie/scatter 四种类型切换,按列特征推断默认类型(日期列→line 时序趋势,低基数分类≤8 类→pie 占比),图表可下载 PNG。

#### Scenario: 多类型图表切换
- GIVEN 查询返回可可视化的结果
- WHEN 结果展示
- THEN 图表区支持 bar/line/pie/scatter 四种类型切换

#### Scenario: 图表类型推断
- GIVEN 结果首列含日期
- WHEN 推断默认类型
- THEN 默认 line(时序趋势)
- GIVEN 结果首列为低基数分类(≤8 类)
- THEN 默认 pie(占比)

#### Scenario: 下载图表 PNG
- GIVEN 图表已渲染
- WHEN 点击下载
- THEN 生成 PNG 并由浏览器下载

### Requirement: 多轮上下文后端补全

前端传 sessionId 但未传 history 时,控制面从该会话消息表取最近 5 条 user 问句补全 history,传给 Agent 的 understand 节点做指代消解。

#### Scenario: sessionId 非空时后端补全 history
- GIVEN 前端传 sessionId 但未传 history
- WHEN 控制面处理查询
- THEN 从消息表取最近 5 条 user 问句作 history,传给 Agent 指代消解

#### Scenario: 不传 sessionId 的临时对话兼容
- GIVEN 调用方不传 sessionId(临时对话)
- WHEN 查询
- THEN 行为与常规查询一致(不落会话消息,history 仍可由调用方显式传)
