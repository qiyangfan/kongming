# external-knowledge spec delta(082-knowledge-tool-multitenant-warning)

## REMOVED Requirements

### Requirement: 部署级注册条件

(081 立的三闸版:端点已配置 + localhost 白名单 + 单租户拒注册。多租户拒注册被
082 证否并废止,整条替换为新名「部署级注册与多租户共享面」。)

## ADDED Requirements

### Requirement: 部署级注册与多租户共享面

系统 SHALL 提供部署级内置工具 `knowledge_search`,其注册 MUST 满足两个条件,缺一不注册:

1. 控制面配置了知识库端点(`KM_SIKU_BASE_URL` 非空)。
2. 端点主机 MUST 为 `127.0.0.1`、`localhost` 或 `host.docker.internal`;其余主机(含任意远端 IP/域名)MUST 拒配并以告警记录原因,不静默忽略。

未注册时,该工具 MUST NOT 出现在任何调用方的工具注入集、工具清单或审计枚举中——对调用方是完全不存在的工具,零回归口径。

个人知识源没有租户语义:多租户部署下所有租户 SHALL 共享同一检索面(同一 vault 内容对全部授权调用方可见)。系统 SHALL 在启动探测到多租户时输出告警明示该共享面,MUST NOT 以此拒绝注册——本能力 v1 定位个人单租户部署,租户级知识源隔离(每租户各配各的端点)不在本能力范围内。

#### Scenario: 未配置端点时工具不存在

- GIVEN 未配置 `KM_SIKU_BASE_URL`
- WHEN 任意用户发起 ReAct 问答
- THEN 注入集不含 `knowledge_search`,行为与引入本能力前逐字节一致

#### Scenario: 非 localhost 端点拒配

- GIVEN 配置 `KM_SIKU_BASE_URL=http://knowledge.example.com:9000`
- WHEN 控制面启动(或配置重载)
- THEN 工具不注册,产生一条告警记录端点主机非法;服务其余部分正常

#### Scenario: 多租户部署告警但注册

- GIVEN 已配置合法端点,且系统中存在 2 个及以上租户
- WHEN 控制面启动
- THEN 工具正常注册(授权调用方注入集可见),并产生一条告警明示所有租户共享同一个人知识源检索面
