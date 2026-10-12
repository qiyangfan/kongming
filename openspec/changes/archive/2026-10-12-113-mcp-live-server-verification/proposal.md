# Proposal

## Why

109 落地的 MCP 出站客户端(HTTP/SSE,httpx 零新依赖实现)已有 42 个测试,
但全部走注入的 mock transport——协议的**真实握手链路**(初始化通知、
SSE 事件流边界、真实服务器的响应头与会话语义)从未与真实 server 验证过。
109 的 admin CRUD 测试也是桩级。作为对外协议客户端,「mock 全绿但没握过
真手」是集成真实性缺口;求职项目语境下,「与真实 MCP server 闭环」也是
可演示的肌肉。

## What Changes

- 新增一个**真实 MCP server 集成测试**(`tests/test_mcp_live.py`):
  测试内用官方 `mcp` python SDK(**仅 dev 依赖**,main 依赖零新增)在
  本地随机端口起一个真实 SSE MCP server(FastMCP,含一个工具/一个
  resource/一个 prompt),经 Kongming **生产** `McpClient`(非测试替身)
  走 probe → initialize 握手 → list_tools → list_resources/list_prompts
  → call 工具闭环,断言真实往返结果。
- 测试基础设施变更:dev 依赖组加 `mcp`;生产代码、契约、schema **零变更**。
- 失败安全:server 起不来/端口占用/SDK 缺失时 skip(不假失败);整体
  带超时,不拖门禁。

## Capabilities

### New Capabilities

(无——纯测试基建,零生产行为变化,skip_specs)

### Modified Capabilities

(无)

## Impact

- `apps/api/pyproject.toml`(dev 组 + `mcp`)与 `uv.lock`。
- 新增 `apps/api/tests/test_mcp_live.py`。
- 不触及:生产代码/契约/schema/前端。门禁时长增量 ≈ 秒级(本地回环)。
