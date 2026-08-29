# 040 · 管理面配置输入防毒化清偿

## Why

管理面四处配置入口让管理员对「封闭集合」手输自由文本：拼写错误的值全部通过语法校验、静默落库，错误后置到远端现场才暴露——RAG schema 拼错要等用户提问执行 SQL 报「schema 不存在」、Bot 工具名拼错被 `resolve_tools` 求交静默丢能力、触发器目标拼错要等运行时派发失败、语义关系表名拼错则该关系永不命中召回（静默死配置）。这些集合全部服务端可枚举，正确形态是「选择框镜像 + 写边界存在性校验」，而不是让管理员背标识符。

## What Changes

- **RAG 知识上传 schema 改选择框**：新增 `GET /system/rag/schemas` 端点返回该数据源可扫描 schema 集合（default 源 = `[tenant_<租户key>, public]`，动态源 = `[default_schema]`，与内省扫描同源同口径）；上传端点新增 schema 存在性校验（∉ 集合 → 400 `SCHEMA_NOT_ALLOWED`），语法校验保留作纵深。**BREAKING**（对 API 直调方）：此前任意合法 PG 标识符可上传，现收敛为封闭集合。
- **Bot 工具改多选**：表单从「逗号分隔自由输入」改为工具注册表多选（含工具描述与写操作标记）；空选合法（零工具 Bot 是 039 既定合法形态，上架闸兜底）。后端不动——`resolve_tools` 交集语义是 039 裁决的漂移容忍设计，维持。
- **触发器目标改联动下拉**：目标标识从自由输入改为按 `target_type` 联动的 workflows / bots 下拉；编辑时目标已删除则降级显示「(已删除) key」占位项防静默改值。创建/更新新增目标存在性校验（400 `TARGET_NOT_FOUND`），与派发期解析同口径。
- **语义关系表/列改选择器**：手工新增关系的左/右表从自由输入改为该数据源知识条目下拉（展示表名+业务别名），JOIN 键改为按所选表知识条目列清单的左/右列下拉（条目缺失时降级输入框）。创建端点新增表存在性校验（400 `RELATION_TABLE_UNKNOWN`）；关系创建逻辑从 router 直连 repo 归位为 service 方法，跨域存在性查询经 port 反转。
- **一致性三小件**（无契约变化）：改密页错误提示补 `role="alert"`；Bot 新建弹窗、反馈处置弹窗包 `<form>` 补齐回车提交（与既有 11 处弹窗一致）；创建密钥复制按钮补「已复制」反馈。
- 契约管线：`contracts/openapi.json` 重导出 + 前端类型再生成（ADR-005）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `rag`：「文档上传与索引」——schema_name 由「语法校验即放行」收敛为「语法 + ∈ 可扫描集合」双闸；新增 schemas 枚举端点需求。
- `triggers-webhooks`：「触发器管理与执行历史」——创建/更新新增目标存在性校验需求与场景；新增 cron 预览端点需求（用户裁决收编，原挂账项）。
- `semantic-layer`：「表关系存储与维护」——手工关系新增表存在性校验需求与场景。

Bot 工具多选、一致性三小件、UsagePage 筛选 combobox 化（候选自既有 summary 响应派生，零契约变化）为纯前端实现/可达性细节，不设 spec delta，落 design/tasks。

## Non-goals

- 数据源连接字段与 default_schema 创建时的自由输入（注册前数据库未连接、无从枚举，形态正确）。
- 记忆「分类」自由文本（开放词表，非封闭集合）。
- Bot 工具后端拒绝未知名（与 039「resolve_tools 交集容忍注册表漂移」裁决冲突；多选控件已在入口消灭手误，后端语义不动）。
- 存量越界 schema 知识条目、已删目标的存量触发器的回溯清理（写边界闸只管增量；历史数据由管理员按既有列表/执行历史自行处置）。
- 触发器目标被删除后的级联禁用/告警（派发失败已有执行历史可见）。

## Impact

- **apps/api**：`rag/`（router 端点+service 校验+repository 抽取可扫描集合计算+测试）、`trigger/`（service 校验+ports 目标存在性端口+cron 预览端点+测试）、`semantic/`（router 归位 service+port 反转+测试）；无表结构变更（不涉 ADR-004）。
- **apps/web**：`RagPage`（schema Select）、`BotsPage`（工具多选+form+复制反馈）、`TriggersPage`（目标联动 Select+cron 预览）、`SemanticPage`（表/列选择器）、`UsagePage`（模型/任务筛选 Select）、`ChangePassword`（role=alert）、`FeedbackPage`（form）；`api-types.gen.ts` 再生成。
- **contracts**：`openapi.json` 新增 GET /system/rag/schemas、GET /system/triggers/cron-preview、三处 400 detail 枚举。
- **不涉**：信任边界（ADR-002，纯控制面内变更）、认证（ADR-003）、schema 迁移（ADR-004）。
