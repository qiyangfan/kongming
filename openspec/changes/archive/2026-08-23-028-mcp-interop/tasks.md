# 028 mcp-interop 实施任务

## 1. 迁移与域包骨架

- [x] 1.1 0016 迁移 sys_mcp_server(ADR-004 约定;tools JSONB;
  headers_cipher;tenant+server_key 唯一)
- [x] 1.2 mcp 域包:models(ServerConfig/DiscoveredTool/命名与冲突
  纯函数)+ports(McpCaller/配置仓储)+repository(008 cipher
  复用加密/掩码查询)+import-linter 契约
- [x] 1.3 权限点种子(system:mcp:manage 20 / tool:mcp:use 21,
  均不进 DEVELOPER 推荐集)+种子测试计数

## 2. JSON-RPC 客户端(测试先行)

- [x] 2.1 失败测试:JSON 响应直读/SSE 帧解析/超时分类/1MB 截断/
  协议错结构化
- [x] 2.2 mcp/client.py(httpx;initialize/tools/list/tools/call;
  15s 默认超时;响应大小闸;凭证不落日志)

## 3. 配置管理与发现(测试先行:安全类)

- [x] 3.1 失败测试:无权限 403/跨租户 404 同形/headers 密文落库+
  掩码回显/key pattern 尾换行绕过/fullmatch
- [x] 3.2 /system/mcp/* 管理端点(CRUD/启停/测试/同步)+operlog
  审计+连通性测试(假 MCP server:测试进程内 FastAPI 子应用)
- [x] 3.3 同步编排(tools/list→冲突跳过告警→快照原子替换;
  单 server 不可达降级记 last_sync_error)

## 4. 执行链接入(测试先行:安全类)

- [x] 4.1 失败测试:tool:mcp:use 未授拒/未标注工具 PENDING_CONFIRM
  +确认后真执行/静态集零回归/server 禁用后 TOOL_NOT_REGISTERED/
  出站超时与截断
- [x] 4.2 DynamicToolResolver 端口+execute_tool 主链接入+dispatch
  MCP handler+目录端点合并(server 溯源字段)
- [x] 4.3 bot tools_for_perms 增 mcp_names 形参(025 调用点兼容)+
  Bot 目录合并

## 5. /mcp 入站(测试先行:安全类)

- [x] 5.1 失败测试:无效/撤销 key 401/跨租户 Bot 不可见/tools/call
  scopes 复校/未知方法 -32601/notifications 202
- [x] 5.2 POST /mcp 路由(initialize/ping/tools/list/tools/call)+
  025 service channel="mcp" 复用

## 6. web

- [x] 6.1 /admin/mcp 子页(列表/对话框 CRUD[headers 键值对]/启停/
  删除受控确认/测试+同步按钮)+导航配置
- [x] 6.2 /admin/tools 目录合并展示;Bots 页目录含 MCP 工具

## 7. 验证与收尾

- [x] 7.1 make check 全绿;make contracts;016 评估 smoke 复验;
  真 LLM 冒烟(假 server 注册→ReAct 调 MCP 工具;真 MCP 客户端
  形态 curl /mcp 全链)
- [x] 7.2 R1 独立评审+修复清零
- [x] 7.3 HANDOFF+挂账(工具结果注入护栏/定时重同步/resources 原语/
  JWKS 入站)+归档
