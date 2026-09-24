# 056-skill-pack 提案:垂类 Skill Pack(术语/示例/人设外挂)

## Why

2C 问数要吃金融、医疗等垂类,但垂类差异(术语口径、黄金问法、回答人设)不应
长成平台代码,也不应继续散落在表注释 / semantic-layer / query_history 三处
拼凑——每上一个垂类就要改 prompt、补表知识、灌历史,无版本、无启停、无法
整包回滚。

业界对照:LangChain tool/MCP 只解决**调用面**;RAGFlow/Dify 的知识库解决
**文档面**。都不覆盖「一个垂类 = 一包可装卸的生成上下文资产」。本变更把
垂类内容收敛为 **Skill Pack 内容包**:术语映射、黄金示例 SQL、回答人设,
按 (租户, 数据源) 启停注入,平台只定义包契约与加载投影,不写死任何垂类。

与既有资产的分工(不推倒、只收口):

| 资产 | 职责 | Skill Pack 关系 |
|---|---|---|
| semantic-layer 指标 | 指标口径表达式,注入生成 | pack 的 metrics 段**激活时写入**语义层,不另建口径库 |
| table_descriptions | 表/列结构知识 | 不动 |
| query_history | 自动成功沉淀 | 不动;pack 的 fewshot 是**人工黄金示例**,与自动沉淀隔离 |

## What Changes

- 新增能力域 `skill-pack`:包定义(manifest + glossary + fewshot + persona)、
  按 (租户, 数据源) 启停、加载后投影到生成链路语义段。
- **包契约(四段,均可空)**:
  1. `manifest`:pack_key、name、version、description;
  2. `glossary[]`:term、aliases[]、definition、hint(口径提示,非 SQL);
  3. `fewshots[]`:question、sql(人工黄金示例,只作 few-shot 文本注入,
     **不直接执行**);
  4. `persona`:回答风格与禁令(注入 summarize/知识问答人设段)。
- 管理面 CRUD + 启停(权限 `system:skill:manage`);启停粒度
  (tenant_id, datasource_key)。
- 生成链路注入:enabled 包合并投影为「术语段 / 黄金示例段 / 人设段」,
  零 enabled 包时链路行为与引入前逐字一致(零回归)。
- pack 的 `metrics[]`(可选第五段)在**启用时**经语义层既有写入口登记
  (metric_name 冲突按 pack_key 前缀避让或拒绝,契约见 design);禁用时
  **不自动删除**语义层指标(避免破坏已依赖查询),仅停止由该包继续维护。
- 契约管线:管理端点入 OpenAPI,`make contracts` 重新生成并提交产物
  (ADR-005)。

## Non-goals

- **不做垂类 Marketplace / 上架审核**:包由平台/租户管理员直接录入,
  分发形态属后续独立演进。
- **不做 LLM 自动编包 / 编译 Wiki**:非确定性生成物入生成上下文与 041/051
  毒知识教训同源;包内容只来自人工录入/导入。
- **不做包内代码执行**:pack 是纯内容资产,不含工具、不含工作流。
- **不做术语 fail-closed 澄清引擎**(「查不到术语必须追问」):v1 只做注入;
  fail-closed 术语工具属独立提案(需工具面配合)。
- **不迁徙既有表知识 / query_history 进 pack**:存量零搬迁。
- **不做 zip 上传 / 多文件包格式**:v1 JSON 表单/接口录入;导入导出属后续。
- **不做跨包优先级/冲突消解的复杂合并语义**:多包同时 enabled 时
  glossary/fewshot 简单拼接(截断封顶),冲突消解不承诺。

## Capabilities

### New Capabilities

- `skill-pack`:Skill Pack 定义与启停、四段(可选五段)内容契约、
  生成链路人设/术语/黄金示例投影、零包零回归、管理与权限。

### Modified Capabilities

(无——semantic-layer / nl2sql-agent 行为由 skill-pack 的投影注入体现,
不改其既有 Requirement 语义;metrics 登记走语义层既有写路径。)

## Impact

- **Schema(ADR-004)**:新表 `skill_pack` + `skill_pack_binding`
  (启停绑定),一次建全(UUID v7 主键、timestamptz、状态 VARCHAR+CHECK);
  迁移走新 Alembic 版本。
- **契约(ADR-005)**:管理 CRUD/启停端点入 OpenAPI,web 类型生成获得。
- `apps/api/kongming`:新域包 `skillpack/`(router→service→models→
  repository),加载投影结果经既有 `/internal/*` 或 retrieve 响应语义段
  下发 Agent(不新增 agent 直连 DB,信任边界 ADR-002 无涉)。
- `apps/agent`:generate/summarize 拼接段消费投影结果,零 enabled 包时
  逐字零回归。
- 不改 SQL 防火墙 / data_scope / 认证(003/006 无涉)。
