## ADDED Requirements

### Requirement: 代码执行工具

内置工具集 SHALL 纯增 run_python_code(cloud-code-sandbox spec 的行为
契约):参数 {code, data_sql?},权限点 tool:code:execute,非写工具
(沙箱无副作用出边界)免确认;注册表/handler 分发/审计/限流与既有
内置工具同构;工具 description SHALL 自带用法(df/pd/np/plt 可用,
data_sql 注入语义),不改动 Agent 面 prompt 资产。

#### Scenario: 注册表同构接入

- GIVEN 授权用户进入 ReAct 模式
- WHEN 工具 schema 注入
- THEN run_python_code 在列,description 含 df 注入用法;
  调用经六层防御与限流,handler 分发到沙箱执行

#### Scenario: 与既有工具零回归

- GIVEN 既有四工具(validate_sql/execute_sql/rag_retrieve/
  create_system_announcement)
- WHEN 本变更上线
- THEN 行为逐字段不变(注册表纯增,无既有键改动)
