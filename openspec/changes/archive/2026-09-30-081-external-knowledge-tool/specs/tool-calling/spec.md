# tool-calling spec delta(081-external-knowledge-tool)

## MODIFIED Requirements

### Requirement: 工具定义与注册表

系统 SHALL 在控制面启动时自动注册一组内置工具,供 LLM 作为可调用函数消费。每个工具的元数据 MUST 包含:唯一名称、自然语言描述(供 LLM 选择)、参数 JSON Schema(供校验与 LLM 函数签名)、所需权限点、是否为写操作(write flag)、幂等策略、是否需人工确认(require_confirm)。工具注册表 SHALL 以控制面为唯一真理源:Agent 面 SHALL NOT 维护独立的工具 schema 清单;控制面调用 Agent 面 ReAct 端点时 SHALL 按调用方授权集注入工具 schema(含写标记与确认标记),未授权工具不出现在注入集中。

注册表 SHALL 支持「部署级可选内置工具」:其注册由部署配置与部署形态条件(见 external-knowledge 能力)决定——条件不满足时该工具不注册、不注入、不出现在任何工具清单,对调用方与 Agent 面零感知。注册表的装配 SHALL 保持为可确定性重放的纯函数(同配置同结果)。

#### Scenario: 启动后内置工具就绪

- GIVEN 控制面与 Agent 面均已启动
- WHEN 任意用户发起 ReAct 模式问答
- THEN 内置工具集合(至少含 validate_sql / execute_sql / rag_retrieve / create_system_announcement)经注入对 LLM 可见,LLM 可在其参数 schema 约束内选择调用

#### Scenario: 工具元数据可作为 LLM 函数签名消费

- GIVEN 一个已注册的工具
- WHEN Agent 面将注入的工具集合以 OpenAI 兼容的 function/tool schema 形式传给 LLM
- THEN LLM 产出的 tool_calls 中的工具名能在注入集中命中,参数结构符合该工具的 JSON Schema

#### Scenario: 未注册工具被拒绝

- GIVEN LLM 产出一个工具名不在注册表中的 tool_call
- WHEN Agent 面将该 tool_call 回调控制面执行
- THEN 控制面拒绝执行,返回明确的错误码,并落审计记录该次越权尝试

#### Scenario: Agent 面不维护独立工具 schema

- GIVEN 控制面新增或变更某工具的参数 schema
- WHEN 不改动 Agent 面任何代码或配置
- THEN 下一次 ReAct 调用即按新 schema 注入,Agent 面无漂移副本

#### Scenario: 部署级工具随配置出现

- GIVEN 部署配置满足某部署级可选工具的注册条件(如已配置知识库端点且单租户)
- WHEN 控制面启动并处理 ReAct 调用
- THEN 该工具与常驻内置工具同范式出现在授权调用方的注入集中(权限过滤、审计、执行路径一致)

#### Scenario: 条件不满足时完全不存在

- GIVEN 部署级可选工具的注册条件不满足(端点未配置/主机非法/多租户)
- WHEN 任意调用方发起 ReAct 问答
- THEN 该工具不出现在注入集与工具清单,未授权回调执行同样被拒并落审计——与「工具从未存在」不可区分
