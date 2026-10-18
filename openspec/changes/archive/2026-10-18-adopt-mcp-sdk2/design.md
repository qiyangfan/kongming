# Design

## Context

mcp-interop 双向现状:出站 `mcp/client.py`(519 行,手写 JSON-RPC 2.0 over
Streamable HTTP,双响应形态解析、113 会话透传、1MB 流闸、异常分类);入站
`mcp/inbound.py`(170 行,手写 initialize/ping/tools/list/tools/call 分发,
协议修订钉 2025-03-26,1MB 闸前置于鉴权)。`test_mcp_live.py` 已用 SDK
FastMCP 起真实 server 验证出站。SDK 2.3 稳定(2026-07-28 规范修订原生支持,
兼容旧修订);2.x 相对 1.x 有 import 路径/API 更名(官方迁移指南逐项对照)。
信任边界(ADR-002)与域纯净(import-linter:mcp 域 models/service/ports 禁
fastapi/sqlalchemy/httpx)是硬约束。

## Goals / Non-Goals

**Goals:**

- 出站/入站协议载体等价替换为官方 SDK,删除两份手写协议代码;
- 跨修订协商与当前修订声明(外部行为),其余行为契约逐字不变;
- SDK 严格限制在 adapter 层(client/router/inbound),域模型零改动。

**Non-Goals:**

- 新原语、resumability、OAuth、入站形态扩展(见 proposal Non-goals);
- 顺带升级任何其他依赖(归 stack-refresh-2026-10 变更)。

## Decisions

**D1 出站=SDK `streamablehttp_client` + `ClientSession`,单次连接生命周期
封装进既有 service 调用形态。** SDK 的会话/传输/修订协商整体承载,替换
client.py 的帧聚合、双形态解析、会话透传;异常在 adapter 层归一为既有
错误分类(不可达/超时/协议错),service 层接口零改动。替代方案=保留手写
仅补新修订支持:等于在 519 行上继续追规范,弃。1MB 流闸语义改为 transport
层的响应体上限守卫(SDK 收整帧消息,守卫在帧级生效,语义等价)。

**D2 入站=SDK server(FastMCP 形态)以子应用挂载,鉴权与闸前置为
ASGI 中间件。** key→Bot 绑定、单 Bot 工具面、tools/call 代理身份语义全部
在 SDK 的 tool provider 回调里实现(薄适配,复用既有 BotService);api-key
校验与 1MB 闸做进挂载中间件,保证「前置于 SDK 路由」的次序语义与 spec
逐字对齐(413/401 同形)。SDK 2 的 server 默认名/启动形态按其迁移指南。

**D3 依赖窗 `mcp>=2.3,<3`,dev→prod 迁移。** 收编后 SDK 是运行时依赖
(出站同步与入站端点都在用),留在 dev 组是 028 时代的历史残留;窗口上限
<3 防未来 major 无感漂移。SDK 2 的传递依赖(starlette/httpx 等)与既有栈
同族,FastAPI 已在其上。

**D4 域纯净分层:SDK import 只允许出现在 `mcp/client.py` 与 `mcp/router.py`
(含重写后的挂载层)。** import-linter 既有契约(mcp.models/service/ports 禁
fastapi/sqlalchemy/httpx)零改动即天然隔离——SDK 不在禁列,但纪律上仍限
adapter;如需更强守护,apply 期评估给 client/router 加 SDK 白名单契约
(可选,不阻塞)。

**D5 测试策略:双面一致性。** ①出站:既有 `test_mcp_live.py` 形态(SDK
FastMCP 起真 server)全量回归 + 新增「新修订 server(无会话)直连」用例;
②入站:新增 SDK 客户端打平台 /mcp 端点的一致性用例(双向都用官方栈,
手写实现的「自我一致性」盲区消除);③安全语义回归:key 撤销 401、租户
隔离、1MB 闸前置、notifications 短路——先写失败测试再动实现(安全规则类
测试先行纪律)。

**D6 迁移次序与回滚。** 先出站后入站(出站风险面小、有 live 测试兜底);
两步各自独立可回滚(单步内 git revert 即回手写版);不设并存开关——
协议载体切换属实现细节,spec 行为不变,无需灰度。

## Risks / Trade-offs

- [SDK 2 API 更名带来迁移面(import 路径、session 用法)] → 迁移指南
  逐项对照;改动集中在两个 adapter 文件,域层零改动使爆炸半径有界。
- [SDK server 的 ASGI 形态与 FastAPI 中间件次序耦合(闸/鉴权前置)] →
  D2 的中间件次序写进测试断言(413 先于 401 先于 body 缓冲)。
- [SDK 会话模型带来连接生命周期变化(长连接 vs 现单请求)] → 同步任务
  内即开即用;不引入常驻连接池(登记候选,出现高频同步场景再评估)。
- [官方 SDK 的行为漂移(2.x minor 变更)] → 版本窗 <3 + uv.lock 钉版;
  升级走既有刷新流程。

## Migration Plan

1. 依赖迁移(dev→prod,>=2.3,<3)与 import 对照表(迁移指南);
2. 出站 client.py 重写(SDK session)→ live 测试回归 + 新修订用例;
3. 入站 inbound.py 重写(SDK server 挂载 + 前置中间件)→ 安全语义测试
   先行回归 + SDK 客户端一致性用例;
4. `make check` 全量 + 真实 MCP server(113 验证过的)端到端复验。
回滚:步骤 2/3 各自独立 revert。

## Open Questions

(无)
