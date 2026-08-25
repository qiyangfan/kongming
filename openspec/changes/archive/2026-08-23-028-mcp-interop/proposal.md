# 028 mcp-interop:MCP 双向互操作

## Why

平台工具生态目前是封闭内置集(019 四件+027 沙箱)。MCP(Model Context
Protocol)已是工具生态事实标准——外部数千个 MCP server 的能力(文件
系统/数据库/SaaS)无法接入,平台的 Bot 能力也无法被 Claude/Cursor 等
MCP 客户端消费。本变更做双向互操作:外部 MCP server 工具桥接进平台
工具注册表(复用 019 六层防御执行),平台 Bot 反向以标准 /mcp 端点
暴露(复用 025 api-key 体系)。让平台「接入世界、被世界接入」(上游
mcp-interop 域的 Kongming 适配)。

## What Changes

- **MCP server 配置管理**(api 新 `mcp` 域包 + 0016 迁移
  sys_mcp_server):租户内 CRUD/启停/删除(server_key/显示名/endpoint/
  自定义 headers),headers 经 008 AES-GCM 加密落库、任何接口不回显
  明文(掩码回显);连通性测试(initialize 握手+tools/list 探测返回
  工具数,结构化错误);新权限点 system:mcp:manage(排序 20);
  操作审计复用 015 operlog。
- **工具发现与动态注册**:同步(手动触发+保存后自动)执行
  tools/list,发现工具以 `mcp__<serverKey>__<toolName>` 命名,快照存
  sys_mcp_server.tools JSONB(description/JSON Schema/readOnlyHint);
  同名冲突后注册方跳过并记告警;server 禁用/删除即工具消失;单 server
  不可达不影响其余(降级记 last_sync_error)。
- **执行纵深防御(复用 019 六层)**:新权限点 tool:mcp:use(排序 21,
  显式授予制);动态工具解析端口(DynamicToolResolver)接入
  execute_tool 主链(静态 REGISTRY 查不到时查租户启用 server 快照);
  参数 JSON Schema 校验用快照 schema;**写判定 fail-closed——无
  readOnlyHint 一律按写工具走 019 确认流**;出站超时(默认 15s)+
  响应 1MB 截断(附截断标记)+审计经 tool_name 前缀溯源 server;
  出站凭证(解密 headers)只在调用瞬时存在,不落任何日志/审计。
- **平台 /mcp 端点(Bot as MCP tools)**:POST /mcp(Streamable HTTP
  单端点,JSON 响应形态),api-key 鉴权(复用 025 sys_api_key 哈希
  校验,撤销即失效);initialize/ping/tools/list/tools/call 四方法+
  notifications 忽略;tools/list 返回 key 所属租户且 scopes 允许的
  启用 Bot(`bot__<botKey>`,inputSchema={question});tools/call 以
  Bot 创建者代理身份经 025 链路运行(channel="mcp" 审计溯源,输入
  护栏/配额/审计不豁免);跨租户 Bot 不可见不可调用。
- **web**:/admin/mcp 第十一子页(列表/新建编辑对话框[headers 键值
  对编辑]/启停 StatusSwitch/删除受控确认/连通性测试+同步按钮);
  019 /admin/tools 目录合并展示 MCP 工具(标记 server 溯源);025
  Bot 编辑器工具目录含 MCP 工具(tools_for_perms 放 mcp__ 前缀
  通配——持 tool:mcp:use 者可选)。
- **传输收敛声明**:仅实现 Streamable HTTP(MCP 2025-03+ 主流);
  上游 spec 的 SSE 传输已被 MCP 官方废弃,不实现。

## Capabilities

### New Capabilities

- `mcp-interop`:MCP server 配置管理、工具发现与动态注册、执行纵深
  防御、/mcp 端点暴露、出站安全约束的行为契约。

### Modified Capabilities

- `tool-calling`:动态工具解析(MCP 工具进 execute_tool 主链)+
  工具目录合并。
- `custom-agent`:Bot 工具目录与绑定含 MCP 工具;channel 枚举增
  "mcp"。

## Non-goals

- 不做 SSE 传输(MCP 官方已弃);不做 MCP 的 resources/prompts
  原语(工具面先行);不做采样(sampling)与 roots。
- 不做 MCP 工具的细粒度单工具权限点(统一 tool:mcp:use;细粒度
  随需求再立);不做出站调用的限流独立配额(复用 019 用户/租户
  限流闸)。
- 不做 /mcp 的会话态(Mcp-Session-Id 有状态协商)——无状态单端点;
  不做 OAuth 2.1 入站鉴权(api-key 先行,JWKS 随后续 SSO 域)。
- 不做 Bot 经 /mcp 的流式输出(tools/call 同步返回结果文本)。
- agent 进程零 MCP 出站(ADR-002 延伸:出站调用 100% 控制面发起,
  agent 零出站凭证);不改 prompts.py。

## Impact

- **代码**:api 新 mcp 域包(models/ports/repository/client/service/
  router)+ toolcall 动态解析与目录合并 + bot 目录通配 + /mcp 入站
  路由;web 新 McpPage + ToolsPage/BotsPage 目录合并。
- **Schema**:0016 迁移 sys_mcp_server(ADR-004 约定;tools JSONB
  快照;headers_cipher 密文列)。
- **契约**:/mcp 端点(JSON-RPC 形态,OpenAPI 记为自由 object)+
  /system/mcp/* 管理端点 + ToolListOut 纯增 server 溯源字段;
  make contracts 重生成。
- **ADR 引用**:ADR-002(agent 零出站)/ADR-004(新迁移)/
  ADR-005(契约)。
- **依赖**:零新依赖——MCP 握手与调用=手写 JSON-RPC 2.0 over
  httpx(主依赖已有;SSE 响应形态自解析,017 分帧经验)。技术栈
  锁定合规(不换库不引库)。
- **安全**:headers AES-GCM(008 cipher 复用)/掩码回显/审计不落
  明文;写判定 fail-closed 确认流;测试先行(权限/租户/确认流/
  截断/撤销 key/跨租户)。
