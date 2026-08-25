## ADDED Requirements

### Requirement: 动态工具解析(MCP 桥接)

execute_tool 主链 SHALL 在静态注册表未命中时查询动态工具解析端口
(mcp-interop spec 的注册面):命中动态工具的语义为 perm_code=
tool:mcp:use、写判定 fail-closed(无 readOnlyHint 即写工具)、
require_confirm 随写判定(走既有确认流);动态工具的 L4 参数校验
使用注册快照的 JSON Schema;未命中任一面 SHALL 维持
TOOL_NOT_REGISTERED 拒绝。工具清单端点 SHALL 合并展示静态内置
工具与当前租户 MCP 工具(MCP 工具带 server 溯源标记)。

#### Scenario: 动态工具进主链

- GIVEN 租户启用 server 注册了 mcp__fs__read_file(只读标注)
- WHEN 授权用户(tool:mcp:use)经 execute_tool 调用
- THEN 六层防御逐层生效,handler 分发至 MCP 出站调用

#### Scenario: 静态集零回归

- GIVEN 无 MCP server 配置的租户
- WHEN 执行工具调用与目录查询
- THEN 行为与 MCP 引入前逐字段一致(静态 REGISTRY 路径不变)
