# 撤下 Siku 外部知识库接入

## Why

Kongming 的首发产品范围是企业数据问答与分析。把独立个人知识库 Siku 接入工具调用链，为 Kongming 增加了对另一个项目接口、配置、权限和出站网关的长期依赖；目前没有足够的产品边界或质量依据支撑这条依赖。按 owner 决策，Kongming 不再集成 Siku。

## What Changes

- 删除 `knowledge_search` 工具及控制面到 Siku `/api/search` 的出站调用。
- 删除 `KM_SIKU_*` 配置、知识域限流配置和对应 RBAC 权限点。
- 从当前行为规范中明确禁止 Siku 专属工具和出站调用，并清理工具调用、网关规范中的 Siku 假设。
- 保留 Kongming 自有 RAG/知识域能力和已归档的 081/082/089 历史工件。

## Non-goals

- 不修改 Siku 仓库或其 API。
- 不改动 Kongming 自有 RAG、文档知识库或知识问答能力。
- 不替换为另一个个人知识库，不做双库融合。
- 不删除现有数据库中的历史工具审计、角色授权或工具域状态数据；这些记录在移除执行路径后不再触发 Siku 调用。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `external-knowledge`: 将 Siku 接入行为改为明确禁止。
- `tool-calling`: 部署级可选工具注册不再引用 Siku。
- `tool-gateway`: 移除 Siku 域与历史迁移要求。

## Impact

- 控制面工具注册、执行分发、出站网关、配置和权限点。
- 当前 OpenSpec 行为契约。
- 不改变 OpenAPI、数据库 schema 或 Agent 面信任边界。信任边界变更参考 ADR-002；权限点按自建认证与 RBAC 约定处理，参考 ADR-003。
