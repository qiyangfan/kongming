# 010 会话体验深化(chat-experience)

## Why

chat 主 spec 已立约的五项行为面中,「结果导出 CSV/Excel」与「EXPLAIN 执行计划」自 005/006 起一直只有契约没有端点;会话标题仍是「首问前 30 字符」的截断毛坯,会话一多即难以辨认;会话列表无搜索、无时间分组,历史会话找回成本高。上游 nl2sql-data-platform 已验证导出/EXPLAIN 的完整形态可直接借鉴,标题 LLM 改写/搜索/时间分组为超越上游的深化(上游三项全空白)。本变更把对话页从「能用」推进到「好用」。

## What Changes

- **会话标题 LLM 改写**:首轮问答成功后,api 经既有内部通道回调 agent 生成简洁标题并回写会话;LLM 失败/超时保持截断标题兜底,零用户感知。初始标题仍为首问前 30 字符(兼容刷新中途场景)。
- **会话重命名**:新增 `PATCH /chat/sessions/{id}` 用户面端点,标题去空白、非空、长度上限 200;前端侧栏下拉菜单接入。
- **会话搜索**:`GET /chat/sessions` 新增可选 `q` 参数,匹配会话标题 OR 会话内 user 问句(ILIKE 字面转义,语义与 007 用户搜索同构),仍按 updated_at 倒序 + limit 收口;前端侧栏搜索框。
- **会话列表时间分组**:前端纯函数分组(今天/昨天/近 7 天/近 30 天/更早),组内保持 updated_at 倒序;零端点变更。
- **结果导出 CSV/Excel**(落地已立约行为):新增 `POST /chat/export`,前端传 columns+rows 不重跑 SQL(与上游同构:所见即所导,数据隔离在查询时已由 data_scope 保证);CSV=UTF-8 BOM+CRLF+RFC 4180 转义;Excel=xlsx 表头加粗+自动列宽;行数截断上限默认 10000(可配);文件名「查询结果-<时间戳>」。
- **EXPLAIN 执行计划**(落地已立约行为):新增 `POST /chat/explain`,**以 message_id 引用**而非接收裸 SQL(优于上游:客户端无法提交任意语句);服务端取该消息落库 SQL → 走与 /internal/sql/execute 全同管线(租户/数据源/绑定复校 → 防火墙初检 → data_scope 改写 → 复检三件套)→ 只读池执行 `EXPLAIN (FORMAT JSON)`;**不放行 ANALYZE**(不真执行,上游防火墙虽放行 ANALYZE 但执行链路从未使用);前端 SQL 折叠卡旁新增「执行计划」懒加载折叠卡,递归缩进渲染计划树。

## Capabilities

### New Capabilities

(无——全部能力归 chat 单一能力域,遵守「一个 change 一个能力域」)

### Modified Capabilities

- `chat`: 修订「会话持久化」的标题规则(截断→截断兜底+LLM 改写)与会话列表场景(时间分组);修订「问答查询端点」中标题描述;新增「会话重命名」「会话搜索」两个 requirement;「EXPLAIN 执行计划」补充「message_id 引用+服务端构造+禁 ANALYZE」scenario;「结果导出 CSV/Excel」补充「不重跑 SQL」scenario。导出/EXPLAIN 主体行为主 spec 已立约,本变更落地端点。

## Non-goals

- **图表增强**(scatter 类型、手动切换 UI、PNG 下载):chat spec「图表可视化」的未兑现残余,与本变更同属对话页但属独立工作包,另起变更评估。
- 流式输出(query-streaming 域)、prompt 护栏、摘要幻觉调优(005 已登记观察)。
- 会话-数据源绑定语义立约(008 挂账⑤):EXPLAIN 取会话创建时的 datasource_key 快照属实现选择,不立约会话绑定。
- 搜索不引入 tsv 列/全文索引/消息结果正文匹配(ILIKE 形态在当前数据量级足够;规模化后随搜索域评估)。
- 导出不含 PNG/PDF/Markdown 等其他格式,不含整会话导出。
- 反馈闭环(feedback/feedback-refine 域)、agent-memory、conversation-artifacts 等上游远期域。
- 会话分享/协作/多设备同步语义变更。

## Impact

- **ADR 引用**:ADR-005(契约管线——新增 3 个用户面端点 + 1 个内部端点,`make contracts` 重生成并提交产物);ADR-002(信任边界——api→agent 新增 `/internal/rewrite-title` 沿用 009 既有内部令牌 LLM 代理模式,agent 零凭证红线不变,无新凭证流向)。ADR-003/004 不触发:认证体系不变(复用 chat:execute,无新权限点);零表结构变更(chat_session.title 等列 baseline 既有,搜索用 ILIKE 不加索引,无新迁移)。
- **apps/api**:chat 域 router/service/repository/models(导出序列化纯函数)、internal/service.py(EXPLAIN 编排复用执行管线)、agent_callback(rewrite_title 调用)、config(export_max_rows 等)、新依赖 openpyxl(xlsx 生成,Python 侧事实标准,上游 POI 对应物;不改变技术栈形态,docs/tech-stack.md 登记)。
- **apps/agent**:新增 `/internal/rewrite-title` 轻量端点(009 describe-table 同构的 LLM 代理),新 prompt(上游无资产,全新撰写,非逐字移植文件)。
- **apps/web**:session-sidebar(搜索框/时间分组/重命名菜单)、assistant-card(导出按钮组+执行计划懒加载卡)、lib/chat-api.ts 封装、blob 下载工具(含 JSON 错误形态检测,上游实证坑)。
- **contracts**:openapi.json 重生成;web 类型全部生成获得。
- **测试**:EXPLAIN 为安全面(复用防火墙/改写管线),测试先行;导出错义/截断/权限、搜索转义/隔离、重命名归属、标题改写降级各配测试。
