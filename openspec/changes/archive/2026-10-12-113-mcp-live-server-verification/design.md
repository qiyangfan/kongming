# Design

## Context

109 的 MCP 客户端(`kongming/mcp/client.py`,httpx 手写 JSON-RPC/SSE)
测试覆盖充分但全为 mock transport。对外协议客户端的真实性缺口:初始化
握手(notify initialized)、SSE 事件流的真实分帧、真实 server 的头/会话
行为,只有真实 server 能验证。

## Goals / Non-Goals

**Goals:**

- 生产 `McpClient` 与**官方 SDK 实现的真实 server**完成一次全量闭环
  (probe → 握手 → 发现 → 调用),作为 109 的集成真实性补强
- 门禁零风险:skip-safe(环境不满足不假失败)、带超时、秒级增量

**Non-Goals:**

- 不引入 streamable-http/stdio 等新传输形态(109 契约=HTTP/SSE,本变更
  只验证既有契约)
- 不把官方 `mcp` SDK 引入 main 依赖(仅 dev 组,给测试起真实 server 用)

**实施修订(如实记录)**:原 Non-goal「不改生产 client」在真实握手下被
证伪——真实 server 暴露两处协议兼容性缺口,均属规范要求而非新形态,按
「根因优先」纳入本变更最小修复:
1. **会话头透传缺失**:streamable-http 规范要求 initialize 后携带
   `Mcp-Session-Id`,生产 client 此前无状态单请求,对 FastMCP 等标准
   stateful server 恒 HTTP 400。修复=响应头有 `mcp-session-id` 则按
   endpoint 存储并在后续请求携带(server 不下发则不带,对无状态形态
   零影响)。
2. **调用面无懒握手**:调用面 client 每请求新建,对 stateful server 的
   任意后续请求都缺会话。修复=公开方法(probe 除外,它显式 initialize)
   入口懒握手(按 endpoint 加锁防并发双重 initialize,已有会话零开销)
   ——标准 MCP 客户端语义。

## Decisions

**D1 真实 server=官方 `mcp` SDK 的 FastMCP(SSE transport),dev 依赖。**
「真实」的判定标准=server 侧由第三方官方实现(非手写桩),这样响应分帧、
头语义、协议细节都来自真实世界。手写 aiohttp 假 server 只是把 mock 换个
地方,不解决真实性问题,否决。dev 依赖与 main 隔离,109「零新依赖」的
主仓契约不破。

**D2 同进程 asyncio task 起服务,随机端口。**FastMCP 的 SSE app 可经
uvicorn 在 127.0.0.1 随机端口拉起(task 形态,测试结束取消);比子进程
形态少一层进程管理,并行 pytest 安全(端口随机)。

**D3 走生产 `McpClient` 全方法面。**probe(健康)、list_tools、
list_resources、read_resource、list_prompts、get_prompt、call——覆盖
109 客户端的全部公开方法,断言真实返回值与注入数据一致(工具回显参数、
resource 内容、prompt 消息)。

**D4 skip-safe。**`mcp` 包缺失(import 失败)或服务起不来 →
`pytest.skip`,与既有 `test_rag_fulltext_config.py` 的 zhparser 探测
skip 形态同款——CI/本机无该 dev 依赖时不假失败。

## Risks / Trade-offs

- **官方 SDK 版本漂移**:SDK 升级可能改 SSE 行为导致本测试红——这是
  期望行为(真实世界变了就该知道),非缺陷。
- **门禁时长**:本地回环 + 秒级,可忽略;整体 asyncio 超时兜底防悬挂。
