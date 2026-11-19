# 拆分超长路由文件与 service 用例簇

## Why

`apps/api/kongming/chat/router.py`（1415 行）把 10 个端点、20 个请求/响应模型、
依赖工厂与 10 个跨域适配器类混装在一个文件里，同步问答与流式问答还各写了一遍
完全相同的 7 段异常→HTTP 翻译，修改时容易漏掉其中一处。同形态的混装文件在
api 面还有六个：

| 文件 | 行数 | 混装内容（AST 实测） |
|---|---|---|
| `kongming/task/router.py` | 1431 | 约 27 个路由 + 30 个模型类 + 2 个适配器类 |
| `kongming/rag/router.py` | 1180 | 约 17 个路由 + 25 个模型类 + 2 个适配器类 |
| `kongming/trigger/router.py` | 946 | 9 个路由 + 6 个模型类 + 4 个适配器类 |
| `kongming/billing/router.py` | 896 | 约 25 个路由 + 20 个模型类 + 1 个适配器类 |
| `kongming/toolcall/router.py` | 880 | 8 个路由 + 9 个模型类 + 8 个适配器类 |
| `kongming/bot/router.py` | 823 | 9 个路由 + 8 个模型类 + 4 个适配器类 |

三个 service 类把多组独立用例混在一个类里（真正的上帝类）：

- `ChatService`（`chat/service.py:240`，1330 行 26 方法）：问答主管线
  （query/start_stream/_finalize 族约 700 行）与会话管理/导出/再执行/EXPLAIN
  （list_sessions/rename_session/session_messages/delete_session/export_result/
  rerun_artifact/explain_message 约 200 行）两组用例共存。
- `TaskService`（`task/service.py:51`，1250 行 30 方法）：任务生命周期
  （create/pause/resume/cancel/step 流转）与多智能体协作（`collab_*` 七方法
  约 350 行 + graph confirm 两方法约 135 行）两组用例共存。
- `RagService`（`rag/service.py:74`，1014 行 26 方法）：知识表管理
  （upload_blocks/edit_table/delete_table/set_ownership/reindex/_ingest 约 400 行）
  与召回管线（recall/recall_probe/_apply_gate/_expand_relations 约 350 行）
  两组用例共存。

混装的代价已经兑现：重复逻辑漂移（chat 两处错误翻译）、查找与修改成本高、
测试接缝（对模块属性的 monkeypatch）散落在长文件各处。本变更做纯结构治理：
按职责把文件拆开、把用例簇分离，行为逐字保持。先例见归档变更
`2026-10-22-separate-escape-and-sql-utils`（跨域结构重构、skip_specs）。

## What Changes

- **批次 1（模式基准）**：`chat/router.py` 拆为四件套——`router.py`（端点 +
  SSE 转发）、`schemas.py`（请求/响应模型与结果转换）、`dependencies.py`
  （依赖工厂与成本闸/断线续存工厂）、`adapters.py`（跨域端口适配器）；
  同步/流式两处相同的异常翻译合并为一个上下文管理器。
- **批次 2–7（机械复制模式）**：task、rag、trigger、billing、toolcall、bot
  六个 router 文件按同一四件套边界拆分；各文件特有的多 router 形态
  （toolcall 的 chat_router 副路由、bot 的 public_router、task/rag 的
  internal_router）与测试接缝按调研清单逐一迁移。
- **批次 8–10（用例簇分离）**：RagService 知识表管理簇、TaskService 协作账本
  与看门狗、ChatService 会话管理/导出簇各自抽出为独立用例模块；双向耦合区
  （TaskService graph 确认/topology、ChatService rerun/explain 终态管线）
  保留并记录理由；ChatService 先加 keyword-only 闸再拆。共享 helper 归属与
  构造签名兼容策略以调研证据定案（见 design.md D6）。
- 全部外部引用（跨模块 import、测试 monkeypatch 靶点、dependency_overrides
  键）同步迁移，不留 re-export 兼容层。
- 每个批次独立跑门禁验收；全量完成后做一次基线对照与契约零漂移核对。

## Capabilities

本变更只调整内部代码职责，不改变任何行为契约：HTTP 路径、状态码、detail 码、
响应体、SSE 事件流、权限点、租户语义全部逐字保持；`contracts/openapi.json`
要求字节级零差异（模型类名不变 → components 不变；端点函数名与注册顺序不变
→ paths/operationId 不变）。无需新增或修改 capability spec，`.openspec.yaml`
标记 `skip_specs: true`。

涉及契约管线断言，引用 [ADR-005](../../../docs/adr/005-contract-pipeline.md)；
适配器迁移不触碰 api↔agent 信任边界，引用
[ADR-002](../../../docs/adr/002-trust-boundary.md) 作为保持性约束；无数据库
schema 变更，ADR-004 不适用。

## Impact

- **代码**：`apps/api/kongming/` 下 chat/task/rag/trigger/billing/toolcall/bot
  七个域包的 router 与三个 service 文件；新增约 25 个模块（每域 schemas/
  dependencies/adapters，service 簇抽出模块）；引用迁移波及 `main.py`、
  bot/store/trigger/workflow 等路由与约 40 个测试文件（精确清单见 design.md
  调研附录与 tasks.md）。
- **兼容性**：对外 HTTP/API 零变化；Python 内部 import 路径变化，全部引用点
  同步迁移。测试对模块属性的 monkeypatch 靶点随组合根迁移（如
  `test_chat_cost_control` 的 `_build_cost_gate`、`test_store` 的
  `get_agent_client`）。
- **契约**：`contracts/openapi.json` 零 diff；前端生成类型无需重跑（重跑亦零差）。
- **验证**：每批次 `make api-check` 对应子集；全量基线对照（拆分前实测：
  1666 通过 / 10 跳过 / 5 个环境依赖失败——4 个 auth federation 出站被本机
  代理截流 + 1 个 STT 真实引擎模型未预下载，均与拆分目标无关；覆盖率 73.77%，
  阈值 70）。
- **文档**：HANDOFF 中指向旧模块路径的口径（如「四个组合根 mcp/bot/chat/toolcall
  router」）同步更新；不新增 ADR（结构拆分为可逆决策，不构成架构定调）。

## Non-goals

- 不改变任何可观察行为：错误码、SSE 帧序与心跳、日志内容与级别、配额/限流
  判定、后台任务触发条件全部保持；logger 名随模块名自然变化（已核实无测试
  断言依赖旧 `*.router` logger 名）；不声称性能收益（未做性能测试）。
- 不拆 agent 进程：`kongming_agent/main.py`（1004 行）虽同形态，但属另一进程、
  另一门禁、helper 形态不同，留待独立批次；`graph/nodes.py`、`llm.py`、
  `apiclient.py` 职责内聚（节点函数集合/单职责客户端/一回调一方法），不拆。
- 不拆仓储：`SqlTaskRepo`（921 行）、`RagRepository`（902 行）、
  `SqlBillingRepo`（765 行）等是同一表组的 SQL 方法集合，方法间零耦合，
  拆分只增加跳转成本。
- 不重命名公开端点函数与路由路径（operationId 不变）；不给旧 import 路径留
  re-export 兼容层。
- 不新增依赖、不引入新框架/中间件；不修改 import-linter 契约的边界定义
  （新模块仍落在原域约束内，领域纯净契约只守护 models/service/ports/export，
  拆分不得把框架依赖拆进这些模块）。
- 不做 web 前端变更；不动数据库 schema 与迁移。
- 不顺带修复拆分过程中发现的行为缺陷——发现即记录、另立变更，保持本变更
  「行为逐字保持」的验收纯度。