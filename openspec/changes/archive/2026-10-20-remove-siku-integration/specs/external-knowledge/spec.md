# Spec Delta: 外部知识库

## ADDED Requirements

### Requirement: 不集成 Siku

Kongming SHALL NOT 注册 Siku 专属工具或实现到 Siku 服务的出站调用。历史 `KM_SIKU_*` 环境变量 MUST NOT 影响工具注册、授权、执行或出站网关。Kongming 自有 RAG 与文档知识能力不受此要求影响。

#### Scenario: 遗留 Siku 配置不启用工具

- **GIVEN** 部署环境仍设置历史 `KM_SIKU_BASE_URL`
- **WHEN** 控制面启动并处理工具调用
- **THEN** 工具注册表不含 Siku 专属工具，且控制面不向该地址发出请求

## REMOVED Requirements

### Requirement: 控制面收口执行

### Requirement: 失败降级为观察

### Requirement: 审计与授权

### Requirement: 部署级注册与多租户共享面
