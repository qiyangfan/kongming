# 设计：拆分超长路由文件与 service 用例簇

## Context

AST 实测（2026-11-19）的混装规模：

| 模块 | 行数 | 端点 | 模型类 | 适配器/工厂 | 外部接缝数 |
|---|---|---|---|---|---|
| chat/router.py | 1415 | 10 | 20 | 10 适配器 + 6 工厂 | ~30 处 import + 2 处模块 patch |
| task/router.py | 1431 | ~27 | 30 | 2 适配器 + 派发链 | 4 处模块 patch + 1 处跨域局部 import |
| rag/router.py | 1180 | ~17 | 25 | 2 适配器 + 2 工厂 | 1 处跨域 import + 1 处私有 import |
| trigger/router.py | 946 | 9 | 6 | 4 适配器 + scheduler 单例 | 4 处私有 import + 3 处出向局部 import |
| billing/router.py | 896 | ~25 | 20 | `_svc` 直调装配 | 仅 main.py 挂载 1 处 |
| toolcall/router.py | 880 | 8 | 9 | 8 适配器 + 网关单例 | 6 处模块 patch + 2 处 override + 7 组 import |
| bot/router.py | 823 | 9 | 8 | 4 适配器 + 2 工厂 | 4 处跨域直调 + override 身份链 |

三个上帝类：ChatService 1330 行 26 方法、TaskService 1250 行 30 方法、
RagService 1014 行 26 方法（方法级耦合分析见附录 H）。

拆分前基线（apps/api 全量 pytest --cov，真实 env 栈 PG18/Redis8）：
**1666 通过 / 10 跳过 / 5 失败，覆盖率 73.77%（阈值 70）**。5 个失败均为
环境依赖、与拆分目标无关，已单跑复核：4 个 auth federation 出站测试被本机
代理截流（httpx InvalidURL），1 个 STT 真实引擎测试因 tiny 模型未预下载
（EngineUnavailable，预下载配方见 docs/HANDOFF.md）。静态门禁
（ruff format/ruff check/mypy strict/pyright strict/lint-imports 27 契约）全绿。

先例：归档变更 `2026-10-22-separate-escape-and-sql-utils`——跨域结构重构、
行为保持、`skip_specs: true`，本变更沿用该形态。

## Goals / Non-goals

**Goals**

- 七个 router 文件按职责拆为 router/schemas/dependencies/adapters（允许按域
  增加模块，见 D2），三个 service 按用例簇分离（见 D6）。
- 全部外部引用同步迁移，不留 re-export 兼容层。
- 行为逐字保持：HTTP 契约字节级零漂移，测试基线对照通过。
- 每个批次独立可验收、可回退（单域文件级变更）。

**Non-goals**

- 不改变任何可观察行为；不声称性能收益（未做性能测试）。
- 不拆 agent 进程、不拆仓储、不动 schema/依赖/web 前端（见 proposal）。
- 不顺带修复行为缺陷——发现即记录另立变更。

## Decisions

### D1：四件套边界与依赖方向

每个域包默认拆为四个模块，依赖方向单向无环：

```
router.py ──→ dependencies.py ──→ adapters.py
    │                │
    └──→ schemas.py ←┘（schemas 不依赖任何本域新模块）
```

- `router.py`：APIRouter 对象（名称冻结，main.py 挂载点不动）、端点函数
  （函数名冻结，operationId 不变）、SSE 帧转发等响应期 helper、端点私有
  校验 helper（如 rag `_doc_name_sensitive`）。
- `schemas.py`：pydantic 请求/响应模型（**类名冻结**，OpenAPI components
  按 `__name__` 生成）、领域结果→响应模型的转换函数、SSE 事件模型与
  responses 文档字典。只 import pydantic 与本域 service/models 的 DTO。
- `dependencies.py`：FastAPI 依赖工厂、服务装配函数、成本闸/限流闸/网关等
  组合根单例与生命周期函数。import adapters，不被 adapters 反向 import。
- `adapters.py`：跨域端口绑定类（`_Internal*Runner`、`_*Adapter` 等）、
  跨域服务装配函数（仅被适配器消费者使用的 `_build_*_service_for`）、
  域异常翻译。不 import dependencies/router（保持无环）。

转换函数改公开名（chat `_out`→`to_chat_query_out`、`_msg_out`→
`to_session_message_out`、`_STREAM_RESPONSES`→`STREAM_RESPONSES`）。
适配器命名按消费面定（apply 期 pyright strict 实证修正）：**被跨模块消费的
适配器类去下划线改公开名**（chat 九个：InternalExplainRunner/
InternalExecuteRunner/ChatUsageCounter/MemoryAdapter/AttachmentChecker/
RegistryToolSpecProvider/PendingToolReader/ChatGuardrail/SchemaOverviewSource）
——pyright reportPrivateUsage/reportUnusedClass 与 HANDOFF「跨模块消费一律
公开名」口径；仅模块内消费的适配器（_TeamRolesAdapter）与装配函数
（_build_connector_service_for/_build_mcp_service_for/_build_cost_gate/
_stream_store_factory）保留下划线，测试消费处按仓库惯例带
`# pyright: reportPrivateUsage=false` 文件头。

### D2：按域的结构偏离（证据驱动，四件套是默认不是教条）

- **task 增 `dispatch.py`**：派发链 `schedule_task_run`(:1295) →
  `_bump_and_schedule`(:1282) → `_schedule_run`(:1301) 在 :1291 按模块全局名
  查找调用，且 `_Collector`(:1340)、`_GEN_TASKS` 强引用集、`_TASK_ROW_SQL`
  构成自包含后台编排单元。整簇迁 `task/dispatch.py`——三个测试文件 patch
  `_schedule_run` 的靶点与 trigger 的跨域 import 同迁一处，全局查找关系
  原样保持。端点经 import 消费派发入口。
- **rag 并入既有 `schemas.py`**：rag 包已有纯 pydantic 响应模型模块
  `rag/schemas.py`（RecallProbeOut 族，消费者 router.py:44 与
  test_rag_probe_schema.py:8）。router 层模型并入该文件，不新建第二个
  schemas 模块。注意 chat/router.py:86 有**同名独立定义**的
  `get_rag_service`（窄装配），与 rag 域工厂不是同一函数，迁移勿混淆。
- **trigger 组合根绑定模块名 `runners.py`（apply 期碰撞修正）**：trigger 包
  已有域纯 `adapters.py`（输入归一化 normalize/normalize_email/AdapterError，
  service.py:15 消费，受零框架契约保护 forbidden fastapi/sqlalchemy/httpx）——
  组合根绑定（HttpxNotifier/ImaplibMailClient/TriggerInputGuardrail/
  TargetRunnerImpl/_bot_question）持框架装配，不可并入，另立 `runners.py`。
  scheduler 单例簇整体迁 dependencies：模块级单例
  `scheduler = TriggerScheduler(service_factory=None)`(:502) +
  `start_scheduler`(:515)/`stop_scheduler`(:520)/`_scheduler_factory`(:507)
  必须同模块保持单例唯一；main.py:72-73 的 lifespan import 随符号改靶。
  `_bot_question`(:58) 仅被 TargetRunnerImpl bot 分支(:491)消费，
  随绑定类迁 runners.py（模块内消费保留下划线）。
- **toolcall 工厂命名空间规则**：`_get_service`(:481) 按模块全局名调
  `_build_websearch_client()`(:496) 与 `_build_connector_gate()`(:498)，
  测试 patch 的是 router 模块属性；`_get_gateway` 是 `@lru_cache(maxsize=1)`
  (:339)，测试调 `.cache_clear()`。因此 `_get_service`、
  `_build_websearch_client`、`_build_connector_gate`、`_get_gateway`、
  `_build_gateway`、`_build_sandbox`、`_build_mcp_service`、
  `load_persisted_domain_states`、`_sync_runtime_disabled` **必须同迁
  dependencies.py**（调用方与被 patch 名同一命名空间），测试靶点整体改指
  `kongming.toolcall.dependencies`。三个 APIRouter（router/internal_router/
  chat_router）留在 router.py。
- **billing 无 DI 工厂**：服务经 `_svc(session, actor)`(:60) 直调装配，
  无 Depends 键、无外部测试接缝。`_svc`/`_channels`/`_pay_http` 迁
  dependencies，`_Oper` 迁 adapters，`_tenant_or_403`/`_payment_out` 按
  消费方归属（校验 helper 留 router，转换迁 schemas）。单 router 无 prefix，
  路径全在装饰器里——装饰器逐字不动。

### D3：符号命名与引用迁移策略

- **不留 re-export**：旧路径符号直接删除，全部消费点改 import 源。理由：
  re-export 会让 monkeypatch 靶点歧义（patch 旧模块属性对经新模块全局名
  的调用失效），且违背仓库「不留兼容层」纪律。
- **冻结面**：APIRouter 对象名、端点函数名、pydantic 模型类名、路由路径与
  装饰器参数（response_model/responses/dependencies/status_code）逐字不动
  ——这四项决定 OpenAPI 字节级输出。
- **迁移面**：跨模块 import（含函数内局部 import）、测试模块别名 patch 靶点、
  dependency_overrides 键的 import 源。override 按函数对象身份匹配，只要
  端点 Depends 与测试 import 指向同一新模块对象即保持一致。
- 逐批接缝清单见附录 A–G，tasks.md 按清单逐项核销。
- **日志口径**：移动代码的 `log = logging.getLogger(__name__)` 随模块改名
  （如 kongming.task.router→kongming.task.dispatch），日志内容与级别不变。
  已核实全部 caplog 断言指向 kongming.main / kongming.rag.rerank /
  kongming.trigger.service / kongming.sqlfw.guard，无一处依赖 `*.router`
  logger 名；无测试 patch router 模块的 log 属性。

### D4：错误翻译合并（仅 chat，仅完全相同块）

chat_query 与 chat_query_stream 的 7 段 except（TenantForbidden→403、
ChatRateLimited→429、ChatQuotaExceeded→429+X-Quota-Warning、
ChatQuotaUnavailable→503、AttachmentNotFound→404、SessionNotFound→404、
InvalidDatasource→400）逐字相同，合并为 router.py 内一个
`@contextmanager def _chat_query_errors()`，两端点以 `with` 包裹服务调用。
异常序、detail 码、`from None`、headers 构造逐字保持；未列出的异常原样穿透。

**其余端点的 except 子集不动**（rerun/sessions/export/explain 各自的异常集
不同，合并会改变捕获面=行为变化）。其他域 router 若存在完全相同块，照此
模式合并；不相同的不碰。

### D5：测试接缝迁移规则

1. **patch 靶点 = 被调方做全局名查找的模块**。消费方经 import 持有函数对象
   不影响 patch（patch 改的是定义模块的全局名）；但「调用时局部 import」
   的消费方（trigger→task/bot/chat）必须从被 patch 的模块取。
   **from-import 陷阱（批次 6 实证）**：端点若以 `from deps import get_gateway`
   绑定原对象，patch deps 模块属性对端点调用失效——可被 patch 的工厂在
   消费模块必须以模块属性访问（`_deps.get_gateway()`）；Depends 身份键
   （get_tool_service）与不被 patch 的函数仍可用 from-import。
2. **lru_cache 单例迁移后仍是同一对象**，`.cache_clear()` 靶点随模块改。
3. **模块级有状态单例**（trigger.scheduler、toolcall._get_gateway、
   task._GEN_TASKS）整簇同迁，禁止跨模块拆分状态与读写方。
4. **dependency_overrides 身份链**：bot `_resolve_agent_client`(:196) 运行时
   读 `app.dependency_overrides[get_agent_client]`——批次 1 迁移
   get_agent_client 时，bot:199 局部 import、conftest 与全部测试 override
   同批改靶，身份链不断。
5. 每批次验收必须包含该域全部接缝测试（附录列名），patch 静默失效不报错、
   只会走真实装配——靠测试断言本身暴露，故全量 pytest 对照是最终防线。

### D6：service 用例簇分离（证据定案）

**RagService（批次 8，切口最干净）**：管理簇与召回簇零方法级互调。

- 抽出 `rag/admin_service.py` → `RagAdminService`：upload_blocks/edit_table/
  delete_table/set_ownership/reindex/_ingest/list_tables/fetch_metadata/
  allowed_schemas/coverage_report 及其独占 helper（_evict_suggestions/
  _embed_quietly/_assert_entry_writable/_assert_team_writable）与独占端口
  （_describer/_suggestion_evictor/_member_roles）。
- 共享 helper `_caller_team_ids`/`_require_usable_datasource` 提为
  `rag/service.py` 模块级函数（持 repo/member_teams 端口参数），两服务单向
  消费（admin→service），不建第三模块。
- `resolve_tenant`（公开、无内部调用方、router 11 处外调）留 RagService。
- writeback/evict_ctx/column_comments 是 chat/feedback 消费的端口实现面，
  留 RagService——**test_rag_recall.py:681 的 `setattr(RagService,
  "writeback", boom)` 与 test_rag_gate.py:346/354 的 `svc._apply_gate`
  直调靶点不变**。
- rag/router 管理端点改经新工厂 `get_rag_admin_service` 装配；召回/探针/
  回写端点不变。chat/feedback/toolcall 消费的窄面不动。

**TaskService（批次 9，双向耦合区保留）**：

- 抽出 `task/collab.py` → `CollabLedgerService`：协作账本五方法
  （collab_start_run/collab_worker_start/collab_worker_result/
  collab_finish_run/collab_record_turn，:720–987）只用 _repo+模块函数，
  干净切出；internal_router 五个 collab 端点改经新工厂装配。
- 抽出 `task/watchdog.py` → `TaskWatchdog`：patrol_stagnation +
  _watchdog_takeover 只依赖 repo。main.py:235 看门狗循环（`_TaskSvc` 别名）
  与 test_task_watchdog.py 8 处单参直构改类名。
- **保留在 TaskService**：graph confirm（suspend/decide）、set_step_topology、
  list_collab_runs、begin_step/apply_plan/cancel/finish——证据：_require/
  _move 被生命周期与 collab/图确认共用，begin_step 反向调 _collab_decision，
  apply_plan 调 set_step_topology，cancel/finish 调 _purge_graph_threads，
  _graph 端口跨簇。行为保持前提下切这块需注入钩子改造，超出本变更纯度，
  记录为后续可选批次。
- 构造签名已经 keyword-only（repo 位置 + bot_resolver/graph_repo kw），不动。

**ChatService（批次 10，先加闸再拆）**：

- **前置硬化的第一步**：__init__ 17 参无 keyword-only 闸，生产组合根按位置
  传前 12 个、test_chat_stream.py:481 按位置传 7 个（含 None 占位）——任何
  参数重排都是静默错位。先加 `*` 闸（repo/agent/ds_checker 之后全部
  keyword-only），同步把三处构造点（chat/dependencies.get_chat_service、
  test_artifacts.py:285、test_chat_stream.py:481）改全关键字传参。此步独立
  可验收（纯签名收紧，调用点同批改）。
- 抽出 `chat/session_service.py` → `ChatSessionService`：list_sessions/
  rename_session/session_messages/delete_session/export_result +
  `_to_message`（staticmethod，仅 session_messages 消费，依赖模块级纯函数，
  零成本搬走）。
- `_resolve_tenant`（仅依赖 repo，:288–299）提为 chat/service.py 模块级
  函数，ChatService 与 ChatSessionService 共用。
- **保留在 ChatService**：query/start_stream 全管线 + rerun_artifact +
  explain_message——证据：rerun 深度复用 _finalize_reply(:1315)/
  _finalize_unavailable(:1291) 终态管线（护栏/评分/列标签/落库全链），
  export 虽可迁但 rerun/explain 与消息归属校验同范式，保留成对。
- 端口清理：export 迁出后 `_oper_logger` 若经 grep 核实零残留消费则从
  ChatService 构造移除（组合根同步）；_explainer/_runner 留（rerun/explain
  消费）。新增 ChatSessionService 持 repo + oper_logger。
- router 五个会话/导出端点改 Depends 新工厂 get_session_service；无测试
  override get_chat_service（替身全在 AgentClient 端口层），DI 图变化零风险。

### D7：import-linter 同步登记（显式登记制，漏登=契约静默失守）

领域纯净契约按模块名列名（pyproject.toml:112/139/161）。每个拆出的
**领域层**新模块必须加进对应契约 source_modules：

- `kongming.chat.session_service` → chat 契约（forbidden fastapi/sqlalchemy）
- `kongming.task.collab`、`kongming.task.watchdog` → task 契约
- `kongming.rag.admin_service` → rag 契约

router 层新模块（schemas/dependencies/adapters/dispatch）允许框架依赖，
不登记。auth 反向依赖契约按整包覆盖，新子模块自动纳入，无需改。
每批次 `uv run lint-imports` 验证。

### D8：TYPE_CHECKING 运行时注解纪律 + AST 守卫扩展

HANDOFF 实证坑（070）：3.14 惰性注解掩盖 TYPE_CHECKING 专有名，3.13 容器
import 即 NameError。迁移规则：

- 签名注解/except 子句/基类/装饰器取用的名字必须运行时导入；仅类型态使用
  的名字保持字符串注解 + TYPE_CHECKING import（如 adapters 的
  `-> "ConnectorService"`/`-> "McpService"` 原样搬迁）。
- 既有守卫 tests/test_module_import_py313.py 只扫 feedback/router.py——
  批次 1 将其泛化为模块清单参数化，纳入全部新组合根模块（每域
  dependencies/adapters），后续批次随新模块递增清单。守卫先于迁移代码
  生效（测试先行）。

### D9：批次顺序与跨批引用交接

顺序：1 chat → 2 task → 3 rag → 4 trigger → 5 bot → 6 toolcall →
7 billing → 8 RagService → 9 TaskService → 10 ChatService。
路由层先行（机械、模式已验证），service 层按切口干净度升风险序。

跨批局部 import 交接（调用时 import 随消费方所在批次或目标批次改靶）：

| 消费点 | 目标符号 | 改靶批次 |
|---|---|---|
| bot/router.py:34,:199 | chat get_agent_client | 1 |
| trigger/router.py:309,:371,:486 | chat get_agent_client | 1 |
| store/router.py:91,:124、workflow/router.py:22、main.py:207 | chat get_agent_client | 1 |
| trigger/router.py:425 | task schedule_task_run | 2 |
| toolcall/router.py:35 | rag get_rag_service | 3 |
| trigger/router.py:485、store/router.py:90,:123、mcp/inbound.py:72 | bot get_bot_service / build_bot_service_with_session | 5 |
| trigger/router.py:442 | TaskService.create（service 不动，仅确认） | 9 |

批次 4 迁移 `_TargetRunnerImpl` 时，其内部 :425/:486 已改靶、:485 仍指
bot.router（批次 5 再改）——每批只改当时存在的引用，交接表核销。

### D10：验收口径

- **每批次**：`ruff format --check` + `ruff check` + `mypy kongming` +
  `pyright` + `lint-imports` + 受影响域测试文件集（附录各节列名），结果
  记入 apply。
- **里程碑（批次 7 后、批次 10 后）**：全量 `pytest -q --cov` 与基线对照
  ——1666 通过 / 10 跳过 / 5 个已登记环境失败，不得新增失败；覆盖率 ≥70
  且不低于基线 73.77%（移动代码不改变覆盖归属，允许 ±0.5% 计量噪声）。
- **契约**：`make contracts-check` 字节级零 diff（批次 1、7、10 后各跑一次；
  模型类名/端点函数名/注册顺序冻结是零 diff 的充分条件）。
- **收尾**：`make check` 全量（api/agent/liannu/web/contracts）一次绿。
- 手工冒烟：本地起 api（env 栈 PG/Redis），真实问答一轮（同步 + 流式 +
  再执行 + 导出 + EXPLAIN），记录于 apply。

## Risks / Trade-offs

- **patch 静默失效（最高风险，toolcall/task）**：靶点模块与全局名查找模块
  不一致时测试不报错、走真实装配。对策：D5 规则 + 附录逐处清单 + 全量对照。
- **ChatService 位置传参错位**：17 参无闸，重排即静默事故。对策：D6 先加
  `*` 闸并同批改三处构造点，独立验收后再拆。
- **OpenAPI 漂移**：模型改名/端点改名/注册顺序变化都会漂移。对策：D3 冻结面
  + contracts-check 三道闸。
- **import-linter 静默失守**：新领域模块漏登记。对策：D7 清单 + 每批
  lint-imports。
- **3.13/3.14 注解语义差**：对策 D8 守卫扩展，守卫先行。
- **文件数增加**：约 25 个新模块，跳转成本上升；换取单文件单职责、接缝
  显式化。接受。
- **并发写者（apply 期实证）**：另一会话同期在主工作区加「结构体积门禁」
  （pylint too-many-lines/too-many-public-methods + ruff C901/PLR0912/PLR0915，
  改 Makefile/ci.yml/pyproject ruff 段/uv.lock），未走占用锁且变更未完结
  （存量 102 处复杂度违规未处理）。按多写者文件切范围纪律：本变更不触碰
  其文件；验证以 `ruff check --ignore C901,PLR0912,PLR0915` 复现快照规则集
  （CLI 覆盖,零文件改动）；pyproject 仅改 importlinter contracts 段并逐 hunk
  复核；收尾 make check 若被其未完结门禁阻塞,如实记录为外部阻塞项。
- **service 拆分不彻底**：TaskService graph/topology 区与 ChatService
  rerun/explain 因双向耦合保留——诚实记录，不为拆而拆引入钩子改造。

## 调研附录：接缝清单（file:line 实测，路径相对 apps/api/）

### A. chat/router.py（主会话自查）

- get_agent_client 消费点（→chat/dependencies）：main.py:207、
  store/router.py:91,124、trigger/router.py:309,371,486、
  workflow/router.py:22、bot/router.py:34,199；tests：conftest.py:244、
  test_chat_cost_control.py:23、test_feedback_submit.py:29、
  test_chat_sessions.py:14、test_chat_datasource.py:12、
  test_billing_sync_usage.py:12、test_metric_provenance.py:27、
  test_chat_quality.py:15、test_bot.py:14、test_feedback_promotion.py:24、
  test_chat_query.py:12、test_store.py:15、test_chat_agent_mode.py:20、
  test_chat_stream.py:24、test_doc_answer_citations.py:27、
  test_datasource_admin.py:458、test_chat_column_labels.py:14、
  test_fallback_persistence.py:14、test_memory.py:13、test_chat_title.py:11、
  test_guardrail.py:15、test_prefs_management.py:14。
- 模块 patch：test_chat_cost_control.py:226,244-247（_build_cost_gate）、
  test_store.py:533-535（get_agent_client）→ 靶点改 chat.dependencies。
- 私有/工厂 import：test_chat_costgate_audit.py:14（_build_cost_gate）、
  test_chat_stream.py:478（_stream_store_factory）、
  test_chat_suggestions.py:15（get_schema_overview/get_suggestor）→
  dependencies；test_http_connector.py:1290（_build_connector_service_for）→
  adapters、:1697（RegistryToolSpecProvider，公开名）→ adapters。
- test_chat_stream.py:21 模块别名仅用 `_relay_gen`(:533)——留 router.py，不改。
- feedback/router.py:44 注释提及 chat/router.get_rag_service → 更新路径。
- 域测试集：test_chat_query/stream/sessions/datasource/agent_mode/quality/
  title/column_labels/export/suggestions/cost_control/costgate_audit、
  test_memory/guardrail/prefs_management/feedback_submit/feedback_promotion/
  artifacts/fallback_persistence/doc_answer_citations/metric_provenance/
  http_connector、test_store/bot/billing_sync_usage/datasource_admin（交叉）。

### B. task/router.py

- main.py:63-64（internal_router/router）——router 对象留 router.py，不改。
- 派发链（→task/dispatch.py）：router.py:1282-1430 整簇
  （_bump_and_schedule/schedule_task_run/_schedule_run/_Collector/
  _GEN_TASKS/_TASK_ROW_SQL）。
- patch 靶点（→task.dispatch）：test_task.py:205,212、
  test_task_collab.py:24,31、test_task_graph_checkpoint.py:18,25
  （_schedule_run）；test_trigger_task_target.py:366,378（schedule_task_run）。
- 跨域消费：trigger/router.py:425 局部 import schedule_task_run → task.dispatch。
- `_get_service`(:66) 端点内直调（非 Depends），无 override。
- 域测试集：test_task、test_task_collab、test_task_graph_checkpoint、
  test_task_readthrough、test_trigger_task_target（交叉）。

### C. rag/router.py

- main.py:52-54（doc_router/internal_router/router）——对象留 router.py。
- get_rag_service(:96) → rag/dependencies；消费点 toolcall/router.py:35
  （顶层 import，:119 直调）改靶。
- `_doc_name_sensitive`(:875) 留 router.py；test_backlog_settlement.py:565
  局部 import 不改路径（符号未迁）。
- 既有 rag/schemas.py 并入 router 层模型；消费者 router.py:44、
  test_rag_probe_schema.py:8 不动。
- 无模块 patch、无 override。
- 域测试集：test_rag_admin/coverage/gate/recall/relation_expansion/
  team_scope、test_document_rag、test_doc_entity_recall、
  test_doc_retrieval_probe、test_backlog_settlement、test_metric_provenance。

### D. trigger/router.py

- main.py:71-74（router/start_scheduler/stop_scheduler/webhook_router）；
  lifespan :334/:359 调 start/stop → 改指 trigger.dependencies；
  router/webhook_router 对象留 router.py。
- scheduler 簇（:502,:507,:515,:520）+ `_make_service`(:237) →
  dependencies.py（单例唯一）。
- 组合根绑定（→runners.py，公开名）：HttpxNotifier、ImaplibMailClient、
  TriggerInputGuardrail、TargetRunnerImpl、_bot_question(:58，模块内消费)。
- 测试私有 import 改靶：test_trigger_task_target.py:320（_make_service→
  dependencies.make_trigger_service 别名）、test_trigger_email.py:409
  （_bot_question→runners）、:589,:617（TriggerInputGuardrail→runners 别名）、
  :949（ImaplibMailClient→runners 别名）。
- _TargetRunnerImpl 出向局部 import：:425（task，批次 2 已改）、
  :485（bot，批次 5 改）、:486（chat，批次 1 已改）——随适配器搬迁。
- 域测试集：test_trigger、test_trigger_email、test_trigger_retry、
  test_trigger_task_target。

### E. bot/router.py

- main.py:30-31（public_router/router）——对象留 router.py。
- 工厂（→bot/dependencies.py，签名与参数顺序冻结）：
  get_bot_service(:164)、build_bot_service_with_session(:181)、
  _resolve_agent_client(:196)、_build_bot_rate_gate(:154)。
- 跨域直调消费点（批次 5 改靶）：store/router.py:90-95,123-126
  （位置传参 session,agent）、trigger/router.py:485-488、
  mcp/inbound.py:72-76。
- override 身份链：_resolve_agent_client:199 局部 import chat
  get_agent_client（批次 1 已改指 chat.dependencies）；conftest.py:248、
  test_bot.py:38、test_billing_sync_usage.py:62、test_store.py:42 的
  override 键同函数对象。
- 适配器（→adapters.py）：_AgentReactRunner、_BotGuardrailAdapter、
  _McpDynamicToolLister、_UsageSinkAdapter。
- 无测试直接 import bot.router 符号。
- 域测试集：test_bot、test_billing_sync_usage、test_billing_bot_quota、
  test_billing_user_team_quota、test_store、test_team、test_mcp（入站段）、
  test_debt_036_api、test_backlog_settlement。

### F. toolcall/router.py

- main.py:67-69（chat_router/internal_router/router）——三对象留 router.py；
  main.py:323 load_persisted_domain_states → toolcall.dependencies。
- 工厂/单例簇（→dependencies.py，同命名空间规则 D2）：_get_service(:481)、
  _build_websearch_client、_build_connector_gate(:478)、_get_gateway(:339,
  lru_cache)、_build_gateway、_build_sandbox、_build_mcp_service、
  load_persisted_domain_states、_sync_runtime_disabled。
- 适配器（→adapters.py）：_InternalSqlExecutor、_RagServiceAdapter、
  _ChatReplyStore、_GuardrailIntentReviewer、_GatewayWebSearchClient、
  _McpDynamicResolver、_ConnectorToolGate、_McpToolCaller。
- 转换（→schemas.py）：_to_out、_audit_item、_summary_ids 按消费方归属。
- patch 靶点（→toolcall.dependencies）：test_toolgateway.py:401,410,452,
  481,505,545；test_websearch_domain.py:797,804,865,870,872,876,880,940,946
  （含 _get_gateway.cache_clear 与 _build_gateway 直调）。
- override/私有 import：test_sandbox.py:241-246,266,271,273、
  test_mcp.py:774-781,805,810,812（_get_service→dependencies；
  _ChatReplyStore/_InternalSqlExecutor/_RagServiceAdapter/_McpDynamicResolver/
  _McpToolCaller→adapters）；test_websearch_domain.py:851
  （_GatewayWebSearchClient→adapters）。
- 域停用状态存于网关单例内部（`gw.apply_disabled`/`gw.summary`，:777-804），
  运行时同步经 `registry.set_runtime_disabled`（外部模块，不动）——router
  无模块级状态字典，工厂簇整迁即保持状态唯一。
- 域测试集：test_toolcall、test_toolgateway、test_websearch_domain、
  test_sandbox、test_mcp、test_http_connector、test_guardrail（意图审核段）。

### G. billing/router.py

- 唯一外部引用 main.py:29（router，无 prefix 单 router）——对象留 router.py。
- `_svc`(:60)/`_channels`(:52)/`_pay_http`(:71) → dependencies；
  `_Oper`(:43) → adapters；`_payment_out`(:193) → schemas；
  `_tenant_or_403`(:82) 留 router。
- 无 patch、无 override、无测试符号 import。
- 域测试集：test_billing、test_billing_admin、test_billing_payment、
  test_billing_reconciliation、test_billing_bot_quota、
  test_billing_user_team_quota。

### H. service 构造点与共享 helper（批次 8–10 证据）

**ChatService**（17 参无 `*` 闸）构造点：chat/router.py:237（12 位置+5 kw）、
test_artifacts.py:285（3 位置+kw）、test_chat_stream.py:481（7 纯位置含 None
占位）。共享 helper：_resolve_tenant（管线+全部 8 个会话方法）、
_finalize_reply/_finalize_unavailable（管线+rerun）、_persist_touch_quietly/
_resolve_labels（经前者间接）。_to_message 仅 session_messages 消费。
端口分组：仅管线 _agent/_history_writer/_stream_store_factory/_tool_specs/
_memory/_attachment_checker/_implicit_memory_task/_cost_gate；仅会话/导出
_oper_logger；仅 rerun/explain _runner/_explainer；共用 _repo/_ds_checker/
_guardrail/_pending_tools/_scoring_task/_column_comments。
无 service 级 fake/subclass/override；替身全在 get_agent_client 端口层。

**TaskService**（repo 位置+kw-only bot_resolver/graph_repo）构造点含别名：
task/router.py:69（全参）、:1406（终态收口）、main.py:235（`_TaskSvc` 看门狗）、
:301（lifespan 清扫）、trigger/router.py:442（立项）、test_task.py:652、
test_task_graph_checkpoint.py:336、test_task_watchdog.py:80,98,116,127,141,
155,168,178（8 处单参 _FakeRepo）。共享 helper：_require（生命周期 5+
collab/图确认 3）、_move（4+1）跨簇；_collab_decision 被 begin_step 反向调；
_purge_graph_threads 被 cancel/finish 调（_graph 端口跨簇）。collab 账本五方法
仅 _repo+模块函数；watchdog 仅 repo。

**RagService**（repo 位置+10 kw-only，3 必填 kw）构造点：rag/router.py:103
（全量）、chat/router.py:92（窄装配 5 kw，缺关系扩展/成员端口——与 rag 版
不同步是既有事实，本变更不改动装配差异）、feedback/router.py:55（4 kw）、
test_rag_relation_expansion.py:367、test_rag_team_scope.py:1072,1087、
test_rag_gate.py:331（全 kw 含 repo=）、test_feedback_promotion.py:400。
类属性 patch：test_rag_recall.py:681（RagService.writeback）；私有直调：
test_rag_gate.py:346,354（_apply_gate）。管理/召回零互调；共享仅
_caller_team_ids/_require_usable_datasource + repo/embedder/cache/member_teams
端口。resolve_tenant 公开、router 11 处外调、无内部调用方。

### 调研闭合核对（2026-11-19 补查，盲点清零）

- **模块级可变状态全扫描**（六 router AST 逐行赋值清点）：仅 task
  `_GEN_TASKS`(:1271) 与 trigger `scheduler`(:502) 两处，均已整簇入设计；
  toolcall 域状态在网关单例内（附录 F）；其余为常量/权限码字符串
  （billing MANAGE/PORTAL 被装饰器定义期求值，留 router.py）。
- **动态访问**：getattr(路由模块别名)/importlib/sys.modules 全仓 grep——
  无 router 模块命中（importlib 命中均为 pypdf/faster_whisper/脚本加载，无关）。
- **扫描规则与 CI**：仓库无 semgrep/ast-grep 配置；.github/workflows
  （ci.yml/eval-nightly.yml）无钉 router.py 路径的规则。
- **日志断言**：caplog 与 log 属性 patch 全扫描，无依赖 `*.router` logger 名
  （见 D3 日志口径）。
- **剩余为 apply 期工作而非调研缺口**：各批次开工通读全文件产出符号→模块
  完整清单（D1/D2 规则已定、隐藏状态簇已预扫描）；service 簇方法体复核
  （tasks 8.1/9.1/10.4 已含验证步，代理结论动手前逐条复核）。

## References

- [ADR-002：信任边界](../../../docs/adr/002-trust-boundary.md)（适配器迁移
  不触碰 api↔agent 边界，保持性约束）
- [ADR-005：契约管线](../../../docs/adr/005-contract-pipeline.md)（OpenAPI
  字节级零漂移断言与 contracts-check 门禁）
- 宪法 Article II（领域纯净，import-linter 守护）、Article V（契约单源）
- 归档先例 `openspec/changes/archive/2026-10-22-separate-escape-and-sql-utils/`
  （跨域结构重构 + skip_specs 形态）
- docs/HANDOFF.md：TYPE_CHECKING 3.13/3.14 注解坑（070 实证）与
  test_module_import_py313.py 守卫；组合根装配口径（mcp/bot/chat/toolcall）
- 三份只读调研报告（2026-11-19，task/rag/trigger、billing/toolcall/bot、
  三大 service），要点已固化于附录 A–H