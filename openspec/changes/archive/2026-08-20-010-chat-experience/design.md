# 010 会话体验深化 — 设计

## Context

chat 域现态(006/008/009 后):4 端点(query/sessions/messages/delete),标题=首问前 30 字符截断(service.py SESSION_TITLE_LEN),`chat_session.datasource_key` 建会话时已落库(repository.py:50-58),消息 `sql_text` 存 agent 产出的**改写前** SQL,改写后形态仅存 sys_sql_audit。执行管线(internal/service.py `execute()`)具备 EXPLAIN 需要的全部防线:租户/数据源解析 → 绑定复校 → 防火墙初检 → data_scope 改写 → 复检三件套 → 只读池(连接级 read-only + 30s statement_timeout)。sqlglot 把 EXPLAIN 解析为 `exp.Command`,现状进 check() 即 BLOCKED_ROOT。agent 面已有 009 铺好的 api→agent 内部 LLM 代理通道范式(/internal/embedding、/internal/describe-table,内部令牌 + httpx)。前端 ChartView 无切换/下载;侧栏无搜索/分组/重命名;无 blob 下载工具。动机见 proposal.md。

## Goals / Non-Goals

**Goals:**
- 五项能力(标题改写/重命名/搜索/时间分组/导出/EXPLAIN)端点与 UI 落地,契约重生成
- EXPLAIN 零防火墙新攻击面(服务端构造,客户端不可提交语句)
- 标题改写在失败/超时时零用户感知,不增加问答主链路延迟
- 导出/EXPLAIN 复用既有权限点与防线,不新增权限点、不动表结构

**Non-Goals:**
- 不改变 agent 8 节点图拓扑(标题端点是图外轻量代理,同 009 describe-table)
- 不引入 tsv/全文索引、不分页器化会话列表(保持 limit 收口)
- 不为导出落 sys_oper_log(用户面查询延伸,与上游一致;数据外发审计随 audit 域评估)
- 图表 scatter/切换/PNG(见 proposal Non-goals)

## Decisions

### D1: 标题 LLM 改写 —— 图外内部端点 + FastAPI BackgroundTasks 条件回写

- **链路**:chat service 在 assistant 落库为 SUCCESS 且「会话标题仍等于截断初始值」时,挂 `BackgroundTasks` 任务 → agent_client 调 `POST /internal/rewrite-title {question}` → agent LLM 生成 → api 回写。
- **回写防覆盖(关键竞态)**:repository `update_title_if_current(session_id, expected=截断值, new)`——`UPDATE ... WHERE id=:id AND title=:expected` 乐观条件更新。用户首轮秒改标题时改写静默失效,人工标题永不被盖。重命名端点走原子归属 UPDATE(显式用户意图,无需乐观锁;R2 对拍注记)。
- **为何 BackgroundTasks 而非 asyncio.create_task**:starlette 托管,响应返回后仍保证 await 完成,测试可控(TestClient 同步语义);create_task 在请求结束事件循环压力下可能被取消。
- **为何不挂 /query 主图**:标题与 SQL 生成无数据依赖,挂图会拉长主链路且让 QueryOut 背新字段;009 describe-table 已验证图外代理范式。
- **净化与截断**:LLM 输出 strip + 去换行 + 去首尾包裹引号(中英文「」""''),cap 50 字符再入库(列上限 200,标题语义宜短)。
- **agent 侧**:新 prompt 全新撰写(上游无此资产,prompts.py 逐字移植纪律不适用);低温(0.3)、max_tokens≤60、短超时;失败/空输出 → agent 返回错误形态,api 兜底保持截断标题不重试。

### D2: EXPLAIN —— message_id 引用 + 服务端构造 + 管线全复用

- **端点**:`POST /chat/explain {message_id}` → `{plan: <pg EXPLAIN JSON>}`。
- **为什么不收裸 SQL(优于上游)**:上游 ExplainCmd{sql,datasourceKey} 允许客户端提交任意文本,防火墙需新增 EXPLAIN 白名单正则;message_id 形态下被分析 SQL 恒为落库的 agent 产出,客户端零提交面,**sqlfw 零改动**(EXPLAIN 前缀在复检三件套之后才拼接,永不进 check())。
- **编排落点(端口反转)**:internal/service.py 新增 `explain()` 公开用例,与 `execute()` 共享私有段(租户解析/动态源解密/绑定复校/初检/改写/复检/只读池连接);chat service 定义 `ExplainRunner`(chat/ports.py,typing.Protocol),组合根(deps/router 装配)绑定 internal.service.explain——与 009 rag⇄datasource「服务层端口反转 + 适配层组合」先例同构,chat.models 零框架契约不破。
- **快照语义**:datasource_key 取消息所属会话的落库值(建会话时已写,008),**不用**当前切换器实时值(同上游「消息级数据源快照」经验);复用 DatasourceChecker 预校,失效/禁用/越属同形 400。此为实现选择,不立约会话-数据源绑定(008 挂账⑤不动)。
- **构造形态**:复检通过后 `EXPLAIN (FORMAT JSON) ` + applied_sql,只读池同连接(SET LOCAL search_path 既有段)执行;返回单行单列 JSON 数组 `[{Plan:...}]`,解析失败降级 `{"raw": text}`(上游为数组包 dict,形状略异,前端两形态均安全降级——R2 对拍注记)。**永不生成 ANALYZE**。
- **审计**:复用 SqlAuditWriter,sql_text=EXPLAIN 形态(可回放),verdict 三态;trace_id 新生成。
- **边界**:消息无 sql_text(纯 FAILED 轮)→ 400 友好;REJECTED 轮有 sql 的走管线会被复检再拦 → BLOCKED 语义返回(不 500);归属失败统一 404(find_session_owned 同构)。

### D3: 导出 —— 纯函数序列化 + 所见即所导

- **端点**:`POST /chat/export {format, columns, rows}` → 文件响应(text/csv / xlsx MIME + `Content-Disposition: attachment; filename*=UTF-8''查询结果-<YYYYMMDD-HHmmss>.<ext>`)。不重跑 SQL(上游同构:数据隔离在查询时已由 data_scope 保证)。
- **纯函数落点**:chat/export.py(零框架新模块,与 models.py 同级):`to_csv(columns, rows) -> bytes`(UTF-8 BOM + CRLF + RFC 4180:含逗号/引号/换行的字段双引号包裹、内部引号双写;None→空串)与 `to_xlsx(columns, rows) -> bytes`。
- **xlsx 依赖**:新增 openpyxl(Python xlsx 事实标准,上游 POI 对应物;纯 python 无系统依赖)。POI autoSizeColumn 对中文不准,openpyxl 无 auto-size——**自算列宽**:每列取表头+前 100 行样本,东亚宽字符按 2 计,width=clamp(max+2, 8, 60)。表头加粗 Font;单元格类型分流:数值 int/float 原生(Decimal 经 005 `_json_cell` 已归一;上游一律 double,Kongming 更保真——R2 对拍注记)、bool 原生、其余 str。
- **截断**:`KM_EXPORT_MAX_ROWS` 默认 10000,超出截断 + warn 日志(与上游 EXPORT_MAX_ROWS 同构;实际导出量≤查询截断 1000,上限纯内存保护)。
- **前端**:postBlob 辅助(api.ts,参照 postForm 模式)+ blob.type==='application/json' 错误形态检测(上游实证:HTTP 200 包错误 JSON 会下载垃圾文件);文件名优先取 Content-Disposition,解析失败回退本地生成。

### D4: 搜索 —— q 参数扩展现有列表端点 + ILIKE 转义上移共享

- **端点**:`GET /chat/sessions?q=&limit=`,q 可选(≤200 字符传输层收口),strip 后为空等价不传(零行为变更,向后兼容)。
- **SQL**:`WHERE user_id AND tenant_id AND (title ILIKE :p ESCAPE '\' OR EXISTS (SELECT 1 FROM chat_message m WHERE m.session_id=s.id AND m.role='USER' AND m.question ILIKE :p ESCAPE '\'))`,排序/上限不变。不加索引(会话表个人量级;挂账规模化)。
- **转义共享**:`_like_pattern`(system/repository.py:30,3 行纯函数)上移至 `kongming/like.py`,system 改引用、chat 直接用——单一真理源,消漂移。这是对 system 域的唯一触碰(等价替换,行为零变化,既有测试锁定)。

### D5: 前端 —— 侧栏三件套 + 消息卡两件套

- **session-sidebar**:搜索框(受控 + 300ms 防抖,react-query 键 `["sessions", tenant, q]` 含搜索维度);时间分组纯函数 `groupSessionsByTime(items, now)`(今天/昨天/近 7 天/近 30 天/更早,按本地日界,组内维持服务端倒序)——纯展示层,零端点变更;条目下拉菜单(DropdownMenu):重命名(Dialog 输入,Enter 提交,与删除确认同构受控模式)/删除。
- **assistant-card**:导出按钮组(CSV/Excel,结果有 columns+rows 时显);「执行计划」折叠卡懒加载(展开才请求,消息 id 为参,失败行内提示不炸卡片)。
- **计划树渲染**:递归 formatPlan 缩进文本入 `<pre>`(上游形态移植:Node Type / cost=Total Cost / rows=Actual Rows 优先 / on Relation Name;无 Plan 字段降级 JSON.stringify),等宽字体 token 化。
- **lib/chat-api.ts**:rename/export/explain 封装(类型全生成,make contracts 后 re-export)。

### D6: 权限与配置

- 全部新端点复用 `chat:execute` + X-Tenant-Id 白名单(与问答同构):导出/EXPLAIN 是查询的读延伸,重命名/搜索是会话管理延伸,均不构成新权限语义——种子零变更。
- config 新增 `export_max_rows`(env `KM_EXPORT_MAX_ROWS`,默认 10000,下界 1);标题改写不加开关(失败兜底天然优雅;agent 不可达时仅 warn 日志)。
- import-linter:chat/export.py、chat/models.py 既有零框架契约自动覆盖新模块(chat 域包整体受守);agent 新端点不引入凭证(ADR-002 红线)。

## Risks / Trade-offs

- [标题后台任务在 agent 宕机窗口内每首个成功轮打一次失败日志] → 仅 warn 不重试,会话标题保持截断值可用;冒烟/演示环境 agent 常开,无实际噪音。
- [EXPLAIN 让规划器看到真实统计信息,计划树含行数估计] → 被分析 SQL 本身是本人权限内已执行语句,信息增量为零;改写防线全复用。
- [openpyxl 新依赖供应链面] → uv.lock 锁定;纯导出路径不触凭证/不触库。
- [搜索 EXISTS 子查询在大消息量下全扫] → 当前会话/消息量级(个人侧栏 ≤200)可忽略;挂账:规模化后评估 tsv/GIN 或独立搜索域。
- [导出无独立审计,数据外发不可见] → 与上游一致;查询入口已有 sql_audit 溯源;oper_log 外发审计挂账 audit 域。
- [时间分组跨时区(服务端 UTC 存储 vs 前端本地日界)] → 分组是展示层模糊语义,本地日界即用户直觉;不做服务端分组。
- [message_id 形态无法对「未落库的临时对话(persist=false)」做 EXPLAIN] → 刻意取舍:临时对话不留痕即无可引用消息,前端对 persist=false 轮隐藏入口(现状 ChatPage 全走 persist 缺省 true,无实际缺口)。

## R1 评审修复注记(实施期独立评审,0 阻断 0 严重)

- **导出 DoS 面收口**:`ExportIn.rows` 加传输层 `max_length=10000`(解析期拒,422)——原截断发生在 JSON 解析之后,挡不住请求体本身内存放大;service 层截断保留为双闸。`export_max_rows` 调大超 10000 时需同步该上限。
- **xlsx 控制字符**:PG text 允许 `\x01` 等 XML 非法字符,openpyxl 遇之抛 `IllegalCharacterError`(裸 500 实证)——文本化时以 `ILLEGAL_CHARACTERS_RE` 剥除;CSV 编码加 `errors="replace"`(孤立代理不炸)。
- **CSV 公式注入中和**:文本值以 `= + - @ \t \r` 开头前置 `'`(OWASP 惯例;内容源自业务库,可能他方写入;Excel 打开按公式/DDE 执行的风险大于前导引号的显示瑕疵)。数值类型不中和。xlsx 路径天然安全(openpyxl 写字符串类型)。
- **title_tasks 死参数**:`rewrite_session_title` 的 `question` 形参从未使用(素材恒为 DB 首问)——删除,调用点同步。
- **`_Prepared.engine` 注解**:object 宽放改 `AsyncEngine`(模块本就有 sqlalchemy import,宽放理由不成立),删两处 `type: ignore`。
- **web 修复**:执行计划失败态永久锁死 → 加重试钮(S1);RenameDialog 补 DialogDescription + 错误 role=alert(S2);搜索换词骨架闪屏 → placeholderData 保留上一档(S3);downloadBlob 锚点入 DOM + setTimeout 延迟 revoke(Safari 兼容,O1);formatPlanNode 非数值 cost 不渲染 NaN(O4)。
- **挂账观察项**:httpx client 构造理论小孔(外层已兜底);人工把标题改成与截断值逐字相同时改写保护失效(概率极低);chat.service 不在 import-linter 契约内(HEAD 既有,因直接 import repository);分组函数 DST 边界(zh-CN 无 DST);删除确认框 busy 期「再想想」可点(删除已发出,结果正确)。

## Migration Plan

零表结构变更、零数据迁移、零种子变更。部署顺序无依赖:api/agent/web 任意先后(新端点未接入前端前不可达;标题改写在 agent 旧版下仅 warn 兜底)。回滚=回退代码,无残留状态。`make contracts` 产物随代码同提交(ADR-005)。

## Open Questions

- 导出/EXPLAIN 是否应落 sys_oper_log(数据外发与计划探测的运营可见性)——随 audit 域变更评估,本变更不落。
- 搜索规模化路径(tsv+GIN / 独立搜索服务)——数据量级触发时再议。
- 标题改写是否对多轮后仍截断标题的老会话补改写(回填策略)——现状仅新会话首个成功轮触发,回填随运营需要另议。
