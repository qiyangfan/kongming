# mcp-interop 规格增量:/mcp 入站请求体大小闸

## MODIFIED Requirements

### Requirement: 平台 /mcp 端点(Bot as MCP tools)

系统 SHALL 在 POST /mcp(Streamable HTTP)以标准 MCP 协议暴露
平台:api-key 鉴权(复用 025 体系——key 绑定单 Bot,撤销/轮换即
失效);tools/list 返回该 key 绑定的启用 Bot(bot__<botKey>,
inputSchema={question: string});绑定 Bot 已停用按 key 无效拒(401,
与 025 OpenAI 端点同语义——resolve_api_key 单点判定);
tools/call 以 Bot 创建者代理身份运行(输入护栏/配额/审计不豁免,
渠道标记 mcp);MCP 客户端 MUST 无法感知其他 key/租户的 Bot;
无 tool:mcp:use 权限不影响本端点(api-key 绑定即授权语义)。
端点 SHALL 设请求体大小闸(上限 1MB):Content-Length 声明值超限
或实测体超限均 413 拒绝;闸 MUST 前置于鉴权、notifications 短路与
body 完整缓冲解析(未认证客户端不可借超大 body 消耗服务端内存)。

#### Scenario: 完整调用链

- GIVEN 租户 T 的有效 api-key(绑定启用 Bot b)
- WHEN 客户端 initialize → tools/list → tools/call
- THEN tools/list 恰返回 1 个 bot__b 工具;tools/call 按 Bot 定义
  运行返回结果文本;调用落审计(channel=mcp)

#### Scenario: 无效或已撤销 api-key

- GIVEN 已撤销或伪造的 key
- WHEN 请求 /mcp
- THEN 401 拒绝,不暴露任何工具清单

#### Scenario: 租户隔离

- GIVEN T1 的 key(绑定 T1 的 Bot)与 T2 的启用 Bot
- WHEN tools/list 或 tools/call(尝试 T2 的 bot__ 名)
- THEN 仅见 key 绑定的 Bot;T2 的 Bot 不可见,调用按未知工具拒

#### Scenario: 超大请求体拒绝(未认证先拒)

- GIVEN 未携带有效 api-key 的客户端
- WHEN POST /mcp 携带 > 1MB 的 JSON body(或 Content-Length 声明值 > 1MB)
- THEN 413 拒绝,先于鉴权与 notifications 短路,body 未被完整缓冲进内存
