# 实施任务

验收口径见 design.md D10；接缝清单见 design.md 附录 A–H（file:line 实测）。
每批次完成后跑静态门禁 + 域测试集并把结果记入 apply；里程碑跑全量对照。

## 0. 基线（已完成）

- [x] 0.1 启动 env 栈 PG（env-postgres）/确认 Redis，跑 apps/api 全量
  `pytest -q --cov`：1666 通过 / 10 跳过 / 5 失败（4 federation 出站被代理
  截流 + 1 STT 模型未预下载，单跑复核为环境依赖），覆盖率 73.77%。
- [x] 0.2 静态门禁基线：ruff format/ruff check/mypy/pyright/lint-imports
  （27 契约）全绿。

## 1. 批次 1：chat/router.py 四件套（模式基准）

- [x] 1.1 扩展 tests/test_module_import_py313.py 为模块清单参数化守卫，
  纳入 chat/dependencies.py 与 chat/adapters.py（先写守卫再迁移，D8）。
- [x] 1.2 新建 chat/schemas.py：迁入全部请求/响应模型与 SSE 事件模型
  （类名冻结），`_out`→`to_chat_query_out`、`_msg_out`→
  `to_session_message_out`、`_STREAM_RESPONSES`→`STREAM_RESPONSES`。
- [x] 1.3 新建 chat/adapters.py：迁入 10 个适配器类 +
  `_build_connector_service_for`/`_build_mcp_service_for` + schema 摘要渲染
  （_render_schema_digest/_SUGGEST_* 常量）；TYPE_CHECKING 字符串注解原样保持。
- [x] 1.4 新建 chat/dependencies.py：迁入 get_agent_client/get_rag_service/
  get_chat_service/get_schema_overview/get_suggestor/get_suggestions_service/
  _build_cost_gate/_stream_store_factory。
- [x] 1.5 router.py 瘦身：留 10 端点 + _sse_frame/_relay_gen；新增
  `_chat_query_errors()` 上下文管理器合并同步/流式两处相同 except 块（D4，
  其余端点 except 子集不动）。
- [x] 1.6 按附录 A 迁移全部 get_agent_client 引用（main/store/trigger/
  workflow/bot + conftest 与 21 个测试文件）；模块 patch 靶点
  （test_chat_cost_control/test_store）改 chat.dependencies；私有工厂 import
  （test_chat_costgate_audit/test_chat_stream/test_chat_suggestions/
  test_http_connector）按归属改靶；feedback/router.py:44 注释更新。
- [x] 1.7 验证：静态门禁全绿 + 附录 A 域测试集全绿 + `make contracts-check`
  零 diff；守卫测试（1.1）通过。

## 2. 批次 2：task/router.py 四件套 + dispatch.py

- [x] 2.1 新建 task/schemas.py（30 个模型类，类名冻结）与 task/adapters.py
  （_CollabBotResolver 等适配器）。
- [x] 2.2 新建 task/dispatch.py：整簇迁入派发链（附录 B：
  _bump_and_schedule/schedule_task_run/_schedule_run/_Collector/_GEN_TASKS/
  _TASK_ROW_SQL），全局名查找关系原样保持。
- [x] 2.3 新建 task/dependencies.py（_get_service 等装配）；router.py 留
  router/internal_router 与 ~27 端点，端点经 import 消费派发入口。
- [x] 2.4 接缝迁移：4 个测试文件 patch 靶点改 task.dispatch（附录 B）；
  trigger/router.py:425 局部 import 改 task.dispatch；守卫清单纳入新模块。
- [x] 2.5 验证：静态门禁 + 附录 B 域测试集（含 test_trigger_task_target
  交叉）全绿。

## 3. 批次 3：rag/router.py 四件套（schemas 并入既有文件）

- [x] 3.1 router 层 25 个模型类并入既有 rag/schemas.py（不新建第二模块，
  既有 RecallProbeOut 族与消费者不动）。
- [x] 3.2 新建 rag/adapters.py（_RelationExpansionAdapter/
  _TeamMembershipAdapter）与 rag/dependencies.py（get_rag_service/
  _get_service）；router.py 留 router/internal_router/doc_router 与端点、
  `_doc_name_sensitive` 留 router。
- [x] 3.3 接缝迁移：toolcall/router.py:35 get_rag_service 改
  rag.dependencies；确认 test_backlog_settlement.py:565 与
  test_rag_probe_schema.py:8 无需改（符号未迁）；守卫清单纳入新模块。
- [x] 3.4 验证：静态门禁 + 附录 C 域测试集全绿。

## 4. 批次 4：trigger/router.py 四件套

- [x] 4.1 新建 trigger/schemas.py（6 模型）与 trigger/runners.py
  （HttpxNotifier/ImaplibMailClient/TriggerInputGuardrail/TargetRunnerImpl/
  _bot_question；碰撞修正：既有域纯 adapters.py 不动，见 design D2；
  出向局部 import :425/:486 随迁，:485 批次 5 改靶）。
- [x] 4.2 新建 trigger/dependencies.py：scheduler 单例簇整迁（:502/:507/
  :515/:520）+ make_trigger_service；router.py 留 router/webhook_router 与端点。
- [x] 4.3 接缝迁移：main.py:72-73 start/stop_scheduler 改
  trigger.dependencies；4 个测试私有 import 按附录 D 改靶；守卫清单纳入。
- [x] 4.4 验证：静态门禁 + 附录 D 域测试集全绿。

## 5. 批次 5：bot/router.py 四件套

- [x] 5.1 新建 bot/schemas.py（8 模型）与 bot/adapters.py（4 适配器）。
- [x] 5.2 新建 bot/dependencies.py：get_bot_service/
  build_bot_service_with_session/_resolve_agent_client/build_bot_rate_gate
  （签名与参数顺序冻结，D5 身份链）；router.py 留 router/public_router。
- [x] 5.3 接缝迁移：store/router.py:90,123、trigger/runners.py:485、
  mcp/inbound.py:72 改 bot.dependencies；确认 override 身份链
  （conftest/test_bot/test_billing_sync_usage/test_store）不断；守卫纳入。
- [x] 5.4 验证：静态门禁 + 附录 E 域测试集（含 store/trigger/mcp 入站交叉）
  全绿。

## 6. 批次 6：toolcall/router.py 四件套（接缝最多）

- [x] 6.1 新建 toolcall/schemas.py（9 模型 + _to_out/_audit_item 等转换）
  与 toolcall/adapters.py（8 适配器类）。
- [x] 6.2 新建 toolcall/dependencies.py：工厂/单例簇**同命名空间**整迁
  （D2：get_tool_service/_build_websearch_client/_build_connector_gate/
  get_gateway(lru_cache)/build_gateway/_build_sandbox/build_mcp_service/
  load_persisted_domain_states/sync_runtime_disabled）；router.py 留三个
  APIRouter 与 8 端点。
- [x] 6.3 接缝迁移：test_toolgateway/test_websearch_domain 共 6 处模块别名
  改 toolcall.dependencies（含 cache_clear/直调靶点）；test_sandbox/
  test_mcp 的 _get_service override 与适配器 import 按附录 F 分迁；
  test_websearch_domain:851 改 adapters；main.py:323 改 dependencies；
  守卫纳入。
- [x] 6.4 验证：静态门禁 + 附录 F 域测试集全绿（重点核对 patch 生效：
  websearch 替身断言不得走真实装配）。

## 7. 批次 7：billing/router.py 四件套 + 路由层里程碑

- [x] 7.1 新建 billing/schemas.py（20 模型 + _payment_out）、
  billing/dependencies.py（_svc/_channels/_pay_http）、billing/adapters.py
  （_Oper）；_tenant_or_403 留 router；装饰器逐字不动（无 prefix 单 router）。
- [x] 7.2 验证：静态门禁 + 附录 G 域测试集全绿；守卫清单纳入。
- [x] 7.3 **里程碑**：全量 `pytest -q --cov` 对照基线（1666/10/5，覆盖率
  ≥73.77%−0.5%）+ `make contracts-check` 零 diff，结果记入 apply。
  实测（批次 10 完成后统一复核）：1691 通过 / 10 跳过 / 5 失败，失败项为
  基线同款环境依赖（4 联邦出站 + 1 STT 模型）；覆盖率 75.72%；契约零 diff。

## 8. 批次 8：RagService 管理簇抽出

- [x] 8.1 `_caller_team_ids`/`_require_usable_datasource` 提为
  rag/service.py 模块级函数（端口参数化），RagService 内部改调函数。
- [x] 8.2 新建 rag/admin_service.py（RagAdminService）：迁入管理簇十方法 +
  独占 helper/端口（D6）；writeback/evict_ctx/column_comments/resolve_tenant
  留 RagService（类属性 patch 与 _apply_gate 直调靶点不变）。
- [x] 8.3 pyproject.toml rag 契约 source_modules 登记
  kongming.rag.admin_service（D7）。
- [x] 8.4 rag/router 管理端点改经新工厂 get_rag_admin_service；召回/探针/
  回写端点与 chat/feedback/toolcall 消费面不动。
- [x] 8.5 验证：静态门禁 + lint-imports + 附录 C 域测试集 +
  test_rag_recall（writeback patch）/test_rag_gate（_apply_gate 直调）/
  test_feedback_promotion 全绿。

## 9. 批次 9：TaskService 协作账本 + 看门狗抽出

- [x] 9.1 新建 task/collab.py（CollabLedgerService）：迁入协作账本五方法
  （仅 _repo+模块函数）；internal_router 五个 collab 端点改经新工厂。
- [x] 9.2 新建 task/watchdog.py（TaskWatchdog）：迁入 patrol_stagnation +
  _watchdog_takeover；main.py:235 看门狗循环与 test_task_watchdog.py 8 处
  直构改类名。
- [x] 9.3 graph confirm/topology/begin_step/apply_plan/终态 purge 保留
  TaskService（双向耦合，D6 记录）；_require/_move 不动。
- [x] 9.4 pyproject.toml task 契约登记 kongming.task.collab、
  kongming.task.watchdog。
- [x] 9.5 验证：静态门禁 + lint-imports + 附录 B 域测试集 +
  test_task_watchdog 全绿；trigger 立项（trigger/router.py:442）与
  lifespan 清扫（main.py:301）回归。

## 10. 批次 10：ChatService 加闸 + 会话管理簇抽出

- [x] 10.1 **先加闸**：ChatService.__init__ 加 `*` keyword-only 闸，三处
  构造点（chat/dependencies.get_chat_service、test_artifacts.py:285、
  test_chat_stream.py:481）改全关键字传参；独立验证（静态门禁 +
  test_chat_stream/test_artifacts + 全 chat 域测试）。
- [x] 10.2 `_resolve_tenant` 提为 chat/service.py 模块级函数，两服务共用。
- [x] 10.3 新建 chat/session_service.py（ChatSessionService）：迁入
  list_sessions/rename_session/session_messages/delete_session/
  export_result + _to_message；持 repo + oper_logger。
- [x] 10.4 grep 核实 _oper_logger 在 ChatService 零残留消费后从构造移除
  （组合根同步）；_explainer/_runner 保留（rerun/explain 留管线侧，D6）。
- [x] 10.5 router 五个会话/导出端点改 Depends get_session_service
  （chat/dependencies 新工厂）。
- [x] 10.6 pyproject.toml chat 契约登记 kongming.chat.session_service。
- [x] 10.7 验证：静态门禁 + lint-imports + 附录 A 域测试集全绿。

## 11. 收尾验收

- [x] 11.1 **里程碑**：全量 `pytest -q --cov` 对照基线（1666/10/5，不得
  新增失败；覆盖率 ≥73.77%−0.5%）+ `make contracts-check` 零 diff。
  首轮全量出现 billing_payment 20 个 setup error 与 test_chat_explain 失败，
  同文件单跑/并跑均全绿、PG 日志无错误；复跑全量得到 1691 通过 / 10 跳过 /
  5 失败（与基线同为 4 联邦出站 + 1 STT 模型环境失败），覆盖率 75.72%，
  判定首轮为环境干扰，非回归。依赖清洞后复跑仍为 1691/10/5、覆盖率
  76.61%（pypdf 7.1.1、certifi、joserfc、opentelemetry-api 升级；沙箱
  `_parse_outcome` 伪造字段逃逸与 team attach 死参数已修）。
- [x] 11.2 `make check` 全量（api/agent/liannu/web/contracts + 新增
  dead-code/dup-check/deps-audit/lint-config）一次绿。
  实测（2026-11-20）：单条 `make check` EXIT=0——api 1719 通过/10 跳过/覆盖率
  76.84%、agent 613、liannu 23、web（typecheck/eslint/madge/stylelint/vitest 183/
  build/size/e2e 2）、contracts 零 diff、dead-code/dup/deps/license/lint-config/
  lock/compose/secret 全绿。此前「本机无法全绿」的根因是 `NO_PROXY` 含
  `[::1],::1`（api-check 未像 agent-check 那样固定白名单），已在 Makefile 修正；
  另修 tests/test_trust_boundary_compose.py 的 pyright 严格类型报错。
- [x] 11.3 手工冒烟：本地起 api（env 栈），真实问答一轮——同步问答、流式
  问答（含心跳/断线续存）、SQL 卡再执行、结果导出、EXPLAIN、会话列表/重命名/
  删除，记录于 apply。
  实测（2026-11-20，api 8000/agent 8001 起当前树、临时账号 smoke1120 用完即删）：
  同步问答 `film` 计数 1000（verdict=PASS）；流式输出 stage/sql/summary/result/
  trace 五类帧、终帧 success=true；流式中途断开（3s 杀连接）后后台续跑，30s 后
  会话消息里 assistant 消息已落库（含 SQL 与摘要）——断线续存成立；EXPLAIN 返回
  Aggregate/Seq Scan 计划；SQL 卡再执行改条件后返 195；CSV 导出带 BOM 与
  Content-Disposition 文件名；会话列表/改名 200、删除 204，冒烟会话全部清空。
  心跳帧未在快速请求窗口内出现（`STREAM_HEARTBEAT_SEC` 空闲才发），由
  test_query_stream 单测覆盖。
- [x] 11.4 HANDOFF 口径更新：组合根模块路径（「四个组合根 mcp/bot/chat/
  toolcall router」等指向旧路径的条目）同步为新模块；登记 5 个环境失败与
  本变更无关的口径。
- [x] 11.5 归档：`openspec validate split-oversized-routers-and-services
  --strict` 通过后按流程归档（skip_specs 已标记）。本地 strict 已通过，
  2026-11-20 归档为 `archive/2026-11-20-split-oversized-routers-and-services`。