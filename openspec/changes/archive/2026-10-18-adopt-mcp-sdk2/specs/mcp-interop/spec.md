# Spec Delta

## MODIFIED Requirements

### Requirement: 工具发现与动态注册

系统 SHALL 对启用状态的 server 执行工具发现(tools/list),发现的
工具以 mcp__<serverKey>__<toolName> 命名注册(description/JSON
Schema/readOnlyHint 快照存库);保存配置与手动触发 SHALL 执行同步;
同名工具冲突时先注册方胜,后注册方 MUST 跳过并记录告警;server
禁用或删除时其工具 MUST 从注册面消失;单 server 不可达 MUST NOT
影响其余 server 与内置工具可用性(降级记录同步错误)。出站协议栈
SHALL 具备跨修订协商能力:对声明 MCP 2026-07-28 修订(无状态形态)
的 server SHALL 无会话握手直连;对旧修订 server SHALL 透明兼容
(含既有会话形态);协议修订不匹配 MUST 归一为同步错误分类,
SHALL NOT 裸抛协议异常。

#### Scenario: 发现并注册进工具目录

- GIVEN 启用且可达的 server 提供 2 个工具
- WHEN 同步完成
- THEN 工具目录出现 2 个 mcp__<key>__* 工具(名称/描述/参数完整)

#### Scenario: 新修订 server 无握手直连

- GIVEN 启用的 server 声明 2026-07-28 修订(无会话握手)
- WHEN 同步执行
- THEN 不发送会话协商请求,工具发现与注册照常完成

#### Scenario: 旧修订 server 透明兼容

- GIVEN 启用的 server 仍为旧修订(会话形态)
- WHEN 同步执行
- THEN 经兼容路径完成会话与发现,行为与本变更前一致

#### Scenario: 断连降级不影响其余

- GIVEN server A 可达、B 断连(B 已注册 3 工具)
- WHEN 重同步
- THEN A 与内置工具正常;B 标记同步失败原因,不抛全局错误

#### Scenario: 禁用后工具消失

- GIVEN server 已注册工具且被禁用
- WHEN 禁用生效
- THEN 其全部 mcp__<key>__* 工具从工具目录消失

### Requirement: 平台 /mcp 端点(Bot as MCP tools)

系统 SHALL 在 POST /mcp(Streamable HTTP)以标准 MCP 协议暴露
平台:api-key 鉴权(复用 025 体系——key 绑定单 Bot,撤销/轮换即
失效);tools/list 返回该 key 绑定的启用 Bot(bot__<botKey>,
inputSchema={question: string});绑定 Bot 已停用按 key 无效拒(401,
与 025 OpenAI 端点同语义——resolve_api_key 单点判定);
tools/call 以 Bot 创建者代理身份运行(输入护栏/配额/审计不豁免,
渠道标记 mcp);MCP 客户端 MUST 无法感知其他 key/租户的 Bot;
无 tool:mcp:use 权限不影响本端点(api-key 绑定即授权语义)。
端点 SHALL 声明 MCP 2026-07-28 修订(无状态形态):initialize
响应的协议修订 SHALL 为当前修订,旧修订客户端 SHALL 经兼容层
互操作(客户端可感知的协议语义不变)。
端点 SHALL 设请求体大小闸(上限 1MB):Content-Length 声明值超限
或实测体超限均 413 拒绝;闸 MUST 前置于鉴权、notifications 短路与
body 完整缓冲解析(未认证客户端不可借超大 body 消耗服务端内存)。

#### Scenario: 完整调用链

- GIVEN 租户 T 的有效 api-key(绑定启用 Bot b)
- WHEN 客户端 initialize → tools/list → tools/call
- THEN tools/list 恰返回 1 个 bot__b 工具;tools/call 按 Bot 定义
    运行返回结果文本;调用落审计(channel=mcp)

#### Scenario: 声明当前修订且旧客户端兼容

- GIVEN 平台端点在线
- WHEN 分别以 2026-07-28 修订客户端与旧修订客户端 initialize
- THEN 新修订客户端握手成功(无会话语义);旧修订客户端同样握手
    成功并完成 tools/list/tools/call 全链

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
