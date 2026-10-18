# Proposal: adopt-mcp-sdk2(MCP 协议栈收编官方 SDK 2.x)

## Why

MCP 互操作双向均为手写协议实现(出站 `mcp/client.py` 519 行手写 JSON-RPC over
Streamable HTTP;入站 `mcp/inbound.py` 170 行手写方法分发,协议修订钉在
2025-03-26)。两条事实使手搓前提失效:①**MCP 规范 2026-07-28 修订将协议改为
无状态**(会话握手废除),官方 Python SDK 2.x(2026-07 稳定)原生支持新修订并
兼容全部旧修订,我们手写的 113 会话透传逻辑维护的是被规范废弃的概念;②手搓
动机「零新依赖」(028 design D1)早已不成立——`mcp` SDK 已是 dev 依赖
(1.30,`test_mcp_live.py` 用 FastMCP 起真实 server 做一致性测试)。协议货币化
风险与双份协议代码维护成本,均由收编官方 SDK 一次性消除。

## What Changes

- **出站收编**:`mcp/client.py`(519 行手写)替换为官方 SDK client session
  (streamablehttp 传输):跨修订协议协商(新修订 server 无握手直连、旧修订
  server 透明兼容)、SSE 帧聚合/双响应形态、错误归一由 SDK 承载;既有行为
  契约不变——工具快照发现、断连降级不影响其余 server、key 掩码、出站白名单。
- **入站收编**:`mcp/inbound.py`(170 行手写)替换为官方 SDK server 挂载
  (FastAPI 路由内):声明当前规范修订(2026-07-28 无状态形态),旧修订
  客户端由 SDK 兼容层透明支持;api-key 鉴权/key 绑定单 Bot/租户隔离/1MB
  大小闸前置语义逐字保留(鉴权与闸在 SDK 路由之前,语义不变)。
- **依赖迁移**:`mcp` 从 dev 依赖转 prod 依赖,版本窗 `>=1.0,<2` →
  `>=2.3,<3`(apps/api);agent 面零变化(MCP 工具调用恒经既有工具回调)。
- **协议修订升级**:平台 /mcp 端点 initialize 响应声明的协议修订从
  2025-03-26 升至当前修订;无会话语义从「平台自选」变为「规范本体」。

## Capabilities

### New Capabilities

(无。)

### Modified Capabilities

- `mcp-interop`:两处行为变化——①「工具发现与动态注册」增加跨修订协议协商
  语义(新修订无握手直连、旧修订透明兼容);②「平台 /mcp 端点」声明修订
  升级至当前规范(无状态形态为规范本体,旧修订客户端仍可互操作)。

## Impact

- `apps/api`:`mcp/client.py`、`mcp/inbound.py` 重写为官方 SDK 适配层
  (adapter 层,import-linter 契约零改动——models/service/ports 不 import SDK);
  `pyproject.toml` 依赖迁移;路由挂载方式微调。
- `apps/agent`:零改动(零凭证不变,ADR-002 边界不动)。
- `contracts/openapi.json`:零变化(/mcp 端点形态不变,非 OpenAPI 面)。
- 测试:`test_mcp_live.py` 升级为双向官方栈一致性测试(SDK 客户端打平台
  端点);既有 mcp-interop 全量场景回归。

## Non-goals

- **MCP 工具面扩容**(sampling/elicitation/roots 等新原语):本变更只换协议
  载体不扩能力面;原语范围沿既有 spec(resources/prompts 消费已由 109 落地)。
- **入站形态扩展**(平台作为 MCP server 暴露 resources/prompts):维持
  Bot as tools 单形态。
- **SDK 高级特性采纳**(resumability 断点续流、OAuth 出站认证):登记候选,
  出现真实 server 场景再评估;本变更只求协议栈等价替换。
- **mcp-interop 域模型改动**:models/service/ports 零改动,SDK 不越出
  adapter 层(宪法 Article II 领域纯净)。
- **agent 面任何变化**。
