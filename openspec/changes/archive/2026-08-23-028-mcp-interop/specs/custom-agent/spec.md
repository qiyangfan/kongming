## ADDED Requirements

### Requirement: Bot 工具目录含 MCP 工具

Bot 编辑器的可选工具目录 SHALL 合并内置工具与租户内已注册 MCP
工具(持 tool:mcp:use 权限的创建者可见);Bot 绑定 MCP 工具后,
运行时经动态解析执行(mcp-interop spec);server 禁用/删除后,已
绑定其工具的 Bot 运行调用该工具 SHALL 被拒并落审计。渠道枚举
SHALL 纯增 "mcp"(/mcp 端点调用溯源)。

#### Scenario: 绑定 MCP 工具的 Bot 运行

- GIVEN 创建者持 tool:mcp:use,Bot 勾选 1 内置工具+1 MCP 工具
- WHEN Bot 运行(任意渠道)
- THEN LLM 可见工具恰为这 2 个;MCP 工具调用经 019 主链执行

#### Scenario: server 禁用后绑定失效

- GIVEN Bot 绑定了 mcp__fs__read_file
- WHEN 该 server 被禁用后运行 Bot
- THEN 调用该工具被拒(TOOL_NOT_REGISTERED),落审计
