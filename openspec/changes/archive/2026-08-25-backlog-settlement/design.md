# Design:backlog-settlement

> 两路代码级核查已完成(逐项文件:行号定位),本设计直接给修复方案与取舍。
> 总原则:零表结构、零新依赖、零新权限点;受控行为变更处同步对齐既有测试断言。

## A 组:安全/正确性

### A1. /mcp 入站 body 闸(B1-B11)

现状:`mcp/inbound.py:43-49` 以 `request_body: dict[str, Any]` 声明,FastAPI
先全量缓冲解析;`rpc_id is None` 的 notifications 在鉴权前 202 短路;main.py
中间件链无 body 限制。未认证客户端可灌任意大 body。

方案:端点签名改收 `request: Request`,按 trigger webhook 范式
(`trigger/router.py:483-486`)双闸——Content-Length 声明值 > 1MB 即 413;
`await request.body()` 实测 > 1MB 即 413;两者都在鉴权、rpc_id 短路与
json.loads 之前。之后手工 `json.loads(raw)`(捕获非 JSON 归协议错误文案,
与既有收敛一致)。闸值复用 trigger 同值 1MB(常量就地定义或共享)。

取舍:不做全局中间件(本包只挡已识别的免登录大开口;/webhook 已有闸、
上传有 10MB 流式闸),scope 最小。

### A2. mark_dormant 收窄(B1-B2)

现状:`memory/repository.py:34-44` UPDATE 有 WHERE(confidence/90 天)但
无 user/tenant 过滤——任一用户召回一次即全局翻牌,与文件头隔离铁律矛盾。

方案:SQL 补 `AND user_id = :u AND tenant_id = :t`;`mark_dormant_lazy()`
签名加两参;调用方 `memory/service.py:56`(recall 路径)本就持有两值直传。
语义从「全局惰性清理」收窄为「本人本租户惰性休眠」——与 spec「召回查询强制
双过滤」同一铁律。不做后台任务(全局清理语义无产品诉求,记忆召回路径天然
覆盖活跃用户)。

### A3. workflow 超时来源区分(B2-B5)

现状:`engine.py:288-305` `except TimeoutError` 一律归 TIMEOUT「超全局预算
中断」。Py3.11+ asyncio.TimeoutError==builtins.TimeoutError,节点内源超时
(驱动语句超时等)被误标;httpx 超时(TimeoutException 非 TimeoutError 族)
却走通用 except 归 FAILED——同为超时,分类不对称。

方案:wait_for 包 task 后以 `task.cancelled()` 判来源:cancelled ⇒ 真·预算
到期(TIMEOUT);否则 ⇒ 内源超时,按 NodeFailed 路径记 FAILED(error 含
「节点内部超时」)。实现:`task = asyncio.create_task(execute_node(...))`,
`try: await asyncio.wait_for(asyncio.shield(task), timeout)` 不行——shield
改变取消语义;直接用「wait_for 返回后 task 状态」不可靠(内源异常时 wait_for
会抛该异常而非 TimeoutError,只有真到期才抛 TimeoutError 且 task 已取消)。
**关键洞察**:wait_for 抛 TimeoutError ⇔ task 被其取消;内源 TimeoutError
透传时 task 未被 wait_for 取消。所以判据=`except TimeoutError` 分支内再
看 task(需 create_task 显式持有):`if task.cancelled(): TIMEOUT else: 内源
FAILED`。注意:create_task 后必须保证异常路径下 task 已结束(wait_for 异常
时 task 要么已带异常完成要么已被取消,无泄漏);CancelledError 语义不受影响。

测试:fake runner 抛 builtins.TimeoutError ⇒ 节点 FAILED 非 TIMEOUT;
真超 remaining ⇒ TIMEOUT。

### A4. _should_skip 收紧+on 校验(B2-B6)

现状:`engine.py:233-246` 判据=「依赖 outputs 有 bool result 键」(非
condition 类型判定),`on` 值不校验("yes" 静默按 true)。顶层与循环子段
两处调用。

方案:`_should_skip(node, dep_outputs, by_id)` 加 `by_id: dict[str, NodeDef]`
参数,仅 `by_id[dep].type == "condition"` 时应用条件门;`validate_dag`
(models.py)对「依赖含 condition 节点的节点」校验 on ∈ {"true","false"},
非法抛 DagValidationError。两处调用点传入 by_id。

既有断言对齐:现有 workflow 测试 DAG 的 on 值均为合法值(核查确认),无对齐
成本;若既有测试有依赖非 condition 前驱且 bool result 的用例——核查未发现
(当前静态节点面只有 condition 产 bool result)。

### A5. ReAct 轮次触顶保留部分结果(A-B6)

现状:`react/nodes.py:251-255` 触顶分支只返回 error+retry_count;早前成功
轮的 last_execution 留在 state 被丢。

方案:触顶分支检查 `state.get("last_execution")`:存在时返回
`{error, retry_count, execution, generated_sql, stats}`(summary 不调 LLM、
置空——触顶不该再花一次模型调用;消费方以 error 优先,rows 作参考)。
`_build_query_out` 不改(直读同名键;success 判定=verdict==PASS——触顶分支
execution 若为 PASS verdict 会 success=True?**不对**,触顶必须呈现为非成功
终态。看 main.py:292-322 的映射:若 execution 在,verdict=execution.verdict
可能=PASS。所以触顶分支构造时 execution 原样携带但顶层 error 非空;
`_build_query_out` 现有逻辑 success = (execution verdict==PASS and not error)?
需核实——若无 error 参与 success 判定,则触顶时 success=True+error 非空是
矛盾形态。**裁决:保持 success=False + rows 携带**——在 _build_query_out
或构造处保证 error 非空 ⇒ success=False,但 columns/rows/generated_sql 照常
映射。消费方(web/bot)已按 error 优先分态渲染,rows 展示为参考数据。

spec 已立约(delta):error 与部分结果并存,消费方以 error 优先。

### A6. doc_name PII 拒绝

现状:`rag/router.py:596-598` 文件名仅查长度;含 PII 文件名原样入库/回显/
进提示词。

方案:上传入口对 `doc_name` 跑 guardrail pii_* 规则集(phone/idcard/
bankcard/email 中选——实际取 masked 检测结果非空即拒),400「文件名含敏感
信息」。**不脱敏**(脱敏名撞 uq_documents_tdq 唯一键+溯源困难)。误伤防线:
bankcard 规则既有 19/20 前缀排除(时间戳防误伤)天然覆盖订单号文件名;
规则评估用「是否命中」而非掩码文本。rag 域 import guardrail 规则——包级
方向:guardrail.rules 是代码注册表纯函数,rag router 组合根 import 无分层
违规(models/service 零框架契约不受影响;与 toolcall router 组合根用
_GuardrailIntentReviewer 同构)。

测试:身份证文件名 400;「orders_20260825_export.md」不误拒。

### A7. bot usage sink 独立短会话(B1-B9)

现状:`bot/router.py:148-162` `_UsageSinkAdapter(session)` 用请求级会话;
agent 分钟级外呼期间占连接;sink 自 commit 提前提交会话挂起写(当前侥幸无)。

方案:照同文件 `_BotGuardrailAdapter`(router.py:101/117)范式——adapter
不收 session,`insert_bot_usage` 内 `async with get_sessionmaker()() as s:
await SqlLlmUsageRepo(s).insert_bot_usage_batch(...)`(该方法自带 commit,
独立会话语义正确)。`_get_service` 构造相应调整。

### B 组:资源/一致性/测试

### B1. LLM 注册表池缓存(021①)

现状:`deps.py:13-16` get_llm 每次新建 LlmClient;`llm.py:93-105` 构造即建
N+2 个 AsyncOpenAI;单次图执行 4-5 套池;无 aclose。

方案:`deps.py` 模块级缓存——key=(settings 指纹, id(event_loop))。
指纹=与 LLM 相关的 settings 字段 tuple(api_key/base_url/registry JSON/
routes JSON/emb 配置/timeout)。指纹或 loop 变更时:旧实例逐 client
`await aclose()` 后重建。提供 `reset_llm_cache()`(测试与 shutdown 用)。
`parse_registry()` 按 registry JSON 串 lru_cache(纯函数,JSON 串即指纹)。
deps.py 头注「刻意不做单例」的既有理由(跨事件循环绑定坑)由 loop 维度
key 化解;头注释同步改写。

测试:同指纹两次 get_llm 同实例;monkeypatch settings 换指纹后新实例且旧
实例 client 已 aclose;不同 loop 各自实例。agent shutdown 不强求接 lifespan
(进程退出即回收;缓存生命周期=进程生命周期,与 settings 热改场景匹配)。

### B2. _tenant_id 收口(B1-B10)

现状:同构 SQL 五份——chat/repository.py(原始定义)、feedback/repository.py
(逐字复制)、memory/router.py、semantic/router.py、bot/router.py。

方案:chat 域 repository 保留唯一 SQL 定义(find_bound_tenant/
find_default_tenant);新建薄 helper(放 `kongming/auth/tenant.py` 或直接在
chat repository 暴露 async 函数):`resolve_tenant_id(session, user_id,
tenant_key) -> str | None`(含 403 语义的包装由各 router 自行判定,helper
只回 None/值)。四处复制改为调 helper(或委托 ChatRepository——六域既有
`_tenant_or_403` 委托范式;此处四份是 router 内联 SQL,统一走 helper 最薄)。

约束:feedback repository 属 domain 层,不得 import auth 包?看依赖方向:
feedback.repository → chat.repository 同层跨域 import 是否允许——既有六域
(trigger/mcp/team/workflow/billing/store)的 `_tenant_or_403` 在 **router**
层委托 ChatRepository(router 组合根跨域组合合法,domain 层零跨域)。
feedback 的复制在 repository(domain 层)——收口方案:feedback router 已有
ChatRepository 注入?若没有,helper 放 domain-neutral 位置。**落地方案:
helper 放 `kongming/tenant.py` 顶层模块(纯 SQL 函数收 session 参数,零框架),
五处全部改调**——避免跨 domain 包依赖争议。核查 import-linter 契约是否允许
(新顶层模块不在任何 forbidden 列表,各域 import 它合法)。

### B3. 收集器早返携带+e2e 测试(035⑧+038⑨+A-B9)

现状:agent main.py 三处早返不带 usage_records——`/query_react` AttachmentError
(452-464)、`/query_react/stream` LlmError(512-517)与 AttachmentError fail
gen(481-486)。当前 bot 不传附件+流式 collect_usage 恒 False ⇒ 不可达,
属潜伏缺陷。

方案:三处统一 `out.usage_records = collector`(构造 out 后挂上再返回/yield)。
e2e 测试:fake LLM 响应对象补 `usage` 属性(FakeLlm 现无),新用例
`collect_usage=True` 跑穿 ReAct 图两轮工具调用 → 断言响应 usage_records
长度=2、seq 0/1、fire-and-forget reporter 零调用(收集器在场时不走上报)。

### B4. 自引用 FK 注入(023①)

现状:`semantic/service.py:48` `if len(tables) >= 2` 与
`semantic/repository.py:67-68` `if len(tables) < 2: return []` 双 guard 拦
单表;introspection SQL 与 ANY(:ts) 天然支持自引用。

方案:两处 guard 放宽为「非空」(`if tables:` / `if not tables: return []`)。
SQL `source_table = ANY(:ts) AND target_table = ANY(:ts)` 不变——单表集合
只命中自引用行,多表行为逐字不变(仅召回集合内关系立约不破)。

测试:表集 ['public.goods'](有自引用 FK 活数据或测试插入 relation 行)时
hint 含自引用形态;多表既有用例回归。

### B5. billing by_model FILTER(038⑥)

现状:`billing/repository.py:273-291` count(*) 不滤 status;金额计算不受
影响(month_token_usage/month_call_count 另有来源),仅账单 usage_json 的
by_model.calls 口径虚高。

方案:SQL 改 `count(*) FILTER (WHERE status = 'SUCCESS') AS calls`,同时
prompt/completion SUM 同口径 FILTER(与 calls 对齐,畸形 ERROR 带回 usage
的 tokens 也不进 by_model——与月账单金额口径(month_token_usage 不过滤,
含 ERROR tokens)有微差?**对齐裁决**:金额=月总 tokens(含 ERROR 实际消耗
理应计费),by_model 是账单明细展示——按 SUCCESS 口径过滤会与金额总额对
不上。**改判:仅 calls 加 FILTER,tokens SUM 保持全量**——calls 语义=
「成功调用次数」,tokens 语义=「实际消耗」与金额口径一致,两语义各自成立,
usage_json 内自洽(by_model tokens 求和=月总 tokens)。测试:造 ERROR 行后
断言 calls 不含、tokens 含(若 ERROR 带 usage)。

## C 组:体验补全

### C1. 用量页租户筛选(038③)

api:`llmusage/router.py` usage_page 加 `tenant_id: uuid | None = None`;
repo `page()` SQL 加 `AND u.tenant_id = CAST(:tid AS uuid)`(**经 u.tenant_id
直滤,不走 LEFT JOIN 的 t 列**——tenant 反解失败的 unknown 行 tenant_key
为 NULL 但 tenant_id 有值,经 t 过滤会漏;经 u 过滤语义精确:按租户 id 筛
即返回该租户全部行含 unknown 归因行。spec delta 已按此口径立约)。
契约重生成。web:UsagePage 筛选区加租户 Select(listTenants 数据源),
queryKey/参数带上。

### C2. 审计页 off-by-one(019⑦/015⑨)

现状:AuditPage.tsx:62/74 end_time 取 `T00:00:00Z` 当日零点,后端
`created_at < :et` 排他——当天全排除。ToolsPage.tsx:46-50 已有 endExclusive
(次日零点)。

方案:endExclusive 抽到共享 util(`src/lib/date-utils.ts` 或并入既有 utils),
ToolsPage 改引,AuditPage 两处替换。纯 web,Playwright 冒烟覆盖。

### C3. 审计页租户筛选+防抖公共 hook(019⑥ 部分/038⑦)

web:AuditPage sql 区加租户 Select(api `/audit/sql/page?tenant_id=` 已备,
audit-api.ts 参数已声明);oper 区无租户列不加。useDebounced 从 UsagePage
抽到 `src/hooks/use-debounced.ts`,UsagePage 改导入(登记条件「出现第二个
消费者」以跨页消费语义触发——本包内 UsagePage/审计页共用时间轴上成立;
即便审计页 Select 不需防抖,抽公共 hook 的时机已成熟,避免第三处再复制)。

## D 组:深挖清偿(收敛指令追加)

### D1. 循环子节点归因粒度(B2-B4)

现状:`engine.py:173-213` `_run_loop` 子节点经 `execute_node(sub,
sub_context)` 内联执行,状态只写进每轮即弃的 sub_context,从不进
node_runs;子节点 NodeFailed 穿透后被顶层 except 归因到 loop 节点。

方案:引擎内建子节点快照——`_run_loop` 内每个子节点执行后构造
NodeRun(node_id=f"{node.id}.{idx}.{sub.id}",status,duration_ms,
error,outputs 沿用节点级截断纪律),append 进顶层 node_runs;
子节点 NodeFailed 先记 FAILED 行再 re-raise(顶层 loop 节点归因行
保留,子节点行已可精确溯源)。快照体积由全局步数上限 200 兜底
(NodeRun 行数 ≤ 步数)。run 快照为 JSONB,零 schema/契约变更;
web 端 nodeRunsOf 窄化展示自然多出新形态行(实施时验证渲染不炸)。

测试:循环第 2 轮子节点失败 → 快照含 `<loop>.1.<sub>` FAILED 行 +
loop 节点 FAILED;成功循环 → 各迭代子节点行逐次记入。

### D2. workflow run 详情 outputs 端点(029⑤)

现状:`page_runs` 用 jsonb_set 剥 outputs 防列表膨胀;无详情端点,
完整 outputs 无处可看。

方案:repo 加 `get_run(tenant_id, wf_key, run_id)`(不剥,返回完整行,
不存在/跨租户 None);service `get_run_detail` 复用 `_assert_visible`
(团队归属同口径);router `GET /system/workflow/{wf_key}/runs/{run_id}`
挂 system:workflow:manage(与 page_runs 同权),response_model 复用既有
WorkflowRunItemOut(列表剥 outputs 后两形态同 schema:outputs 可空——列表
为 null、详情为全量;ADR-005 契约化满足,不另立 Detail 类型)。契约重生成。
web:WorkflowsPage runs 历史行加「详情」按钮 → Dialog 按需拉取,
渲染 node_runs 列表(节点 id/状态/耗时/错误/outputs 折叠 JSON 展示,
复用既折叠形态)。outputs 展示按需拉取不进列表,膨胀面关闭。

### D3. 全站查询错误态统一(038⑧)

现状:038 只修了用量页 isError(role=alert);存量 15+ 页面查询失败
仍静默空白或伪装空态。

方案:抽 `src/components/query-error.tsx` 共享组件(Props:文案可选,
默认「加载失败,请稍后重试」;role=alert;视觉用 destructive/警示
token 形态与 038 用量页一致);全站 grep useQuery/isError 消费面,
凡缺显式错误态的页面统一替换为该组件(实施时先出清单再批改);
UsagePage 的就地实现一并收敛到组件。web-nav spec 立约(delta 已备)。
机械批改,Playwright 冒烟抽查两页(断服务制造 5xx 看渲染)。

## 不修项的终态处置(收敛)

- A-B10:结案——api write_burst 写闸实证兜底,残留仅 token 成本。
- B2-B9:结案——h11 16KB 兜底实证,http_runner 补依赖注释。
- 审计页 user 筛选 UI:结案——API 能力已备,UI 场景低频不做。
- D-B7:结案——原审查已裁接受。
- 条件触发登记/产品登记清单见 proposal 终态处置节;HANDOFF 更新时
  将「已知 TODO」各段按四类终态重构(开放挂账清零)。

## 测试与验证策略

- 安全类(mcp 闸/doc_name/dormant)测试先行。
- 受控行为变更对齐既有断言:billing FILTER(造 ERROR 行断言)、doc_name
  拒绝(既有上传测试文件名避 PII)、_should_skip(既有 DAG 均合法 on 值)、
  dormant(既有用例若断言全局翻牌需改为断言收窄语义)。
- make check 全绿 + make contracts 重生成(llm-usage page 参数)。
- 冒烟:真环境跑 /mcp 超限 413、上传 PII 文件名 400、ReAct 触顶带部分结果
  (或以单测直证)、usage 页租户筛选 UI、审计页结束日含当天。
