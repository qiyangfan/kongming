# external-knowledge Specification

## Purpose

本能力登记 Kongming 对外部个人知识库(Siku)接入的否定契约:Siku 专属工具与出站调用不属于 Kongming 能力范围。Kongming 自有的 RAG 表说明召回、文档知识库(document-rag)与知识问答能力不受本约束影响,各自规格单独生效。

## Requirements

### Requirement: 不集成 Siku

Kongming SHALL NOT 注册 Siku 专属工具或实现到 Siku 服务的出站调用。历史 `KM_SIKU_*` 环境变量 MUST NOT 影响工具注册、授权、执行或出站网关。Kongming 自有 RAG 与文档知识能力不受此要求影响。

#### Scenario: 遗留 Siku 配置不启用工具

- **GIVEN** 部署环境仍设置历史 `KM_SIKU_BASE_URL`
- **WHEN** 控制面启动并处理工具调用
- **THEN** 工具注册表不含 Siku 专属工具，且控制面不向该地址发出请求
