## Purpose

MCP(Model Context Protocol)双向互操作:外部 MCP server 的工具经
配置桥接进平台工具注册表(复用工具纵深防御执行),平台 Bot 反向以
标准 /mcp 端点暴露给 MCP 客户端。让平台「接入世界、被世界接入」。

## Requirements

### Requirement: MCP server 配置管理

持 system:mcp:manage 权限点的用户 SHALL 能在租户内管理外部 MCP
server 配置:新增/编辑/启停/删除(server_key/显示名/endpoint/自定义
headers),传输类型仅 Streamable HTTP。系统 SHALL 提供连通性测试
(initialize 握手+tools/list 探测返回工具数;失败返回结构化错误,
不暴露内部堆栈)。自定义 headers MUST 加密落库(AES-GCM),任何
查询接口 MUST NOT 回显明文(掩码形态,键名可见值为 ***);编辑时
未改动的 header 键值留空即保持现值。配置 MUST 按租户隔离(跨租户
404 同形),管理操作 MUST 落操作审计。

#### Scenario: 创建并连通性测试

- GIVEN 持 system:mcp:manage 的用户
- WHEN 录入外部 MCP server(endpoint+鉴权 headers)并触发连通性测试
- THEN 系统执行握手与工具发现,返回可达状态与工具数;连通失败返回
  结构化错误(超时/拒连/协议错分类),不暴露内部堆栈

#### Scenario: headers 不回显明文

- GIVEN 已配置鉴权 headers 的 server
- WHEN 查询配置详情/列表
- THEN headers 值以 *** 掩码返回,明文不出现在任何响应/日志/审计中

#### Scenario: 越权与租户隔离

- GIVEN 未授权用户,或另一租户用户
- WHEN 调用管理端点
- THEN 403/404 同形拒绝

### Requirement: 工具发现与动态注册

系统 SHALL 对启用状态的 server 执行工具发现(tools/list),发现的
工具以 mcp__<serverKey>__<toolName> 命名注册(description/JSON
Schema/readOnlyHint 快照存库);保存配置与手动触发 SHALL 执行同步;
同名工具冲突时先注册方胜,后注册方 MUST 跳过并记录告警;server
禁用或删除时其工具 MUST 从注册面消失;单 server 不可达 MUST NOT
影响其余 server 与内置工具可用性(降级记录同步错误)。

#### Scenario: 发现并注册进工具目录

- GIVEN 启用且可达的 server 提供 2 个工具
- WHEN 同步完成
- THEN 工具目录出现 2 个 mcp__<key>__* 工具(名称/描述/参数完整)

#### Scenario: 断连降级不影响其余

- GIVEN server A 可达、B 断连(B 已注册 3 工具)
- WHEN 重同步
- THEN A 与内置工具正常;B 标记同步失败原因,不抛全局错误

#### Scenario: 禁用后工具消失

- GIVEN 已注册工具的 server
- WHEN 管理员禁用
- THEN 其全部工具从目录与 Bot 可选集移除;运行时调用被拒落审计

### Requirement: MCP 工具执行纵深防御

MCP 工具调用 MUST 复用 019 六层防御主链:接口权限点 tool:mcp:use
(显式授予制)+ 租户校验 + 参数 JSON Schema 校验(快照 schema)+
审计落库(tool_name 前缀溯源 server)+ 用户/租户限流 + 出站超时
(默认 15s)与响应大小上限(1MB,截断附标记并记原始大小)。写/读
判定 MUST fail-closed:无 readOnlyHint 标注的工具按写工具走 019
人工确认流。执行失败(超时/断连/协议错误)MUST 以结构化错误回灌
agent 循环,MUST NOT 静默吞掉。出站调用 MUST 全部由控制面发起
(agent 零 MCP 出站、零出站凭证);出站凭证 MUST NOT 落日志/审计。

#### Scenario: 授权调用成功并落审计

- GIVEN 用户持 tool:mcp:use,调用只读标注 MCP 工具
- WHEN 执行
- THEN 经权限/租户/schema 校验后出站执行,结果截断上限内回灌;
  审计含 server 溯源与耗时

#### Scenario: 未标注工具走确认流(fail-closed)

- GIVEN MCP 工具未提供 readOnlyHint
- WHEN agent 调用
- THEN 按写工具处理:下发确认请求,确认前不产生出站调用;
  确认后重放经同一执行链

#### Scenario: 超大结果截断

- GIVEN MCP 工具返回超 1MB 的结果
- WHEN 执行完成
- THEN 结果截断至上限并附截断标记与原始大小回灌(回灌结果含
  truncated/original_size 字段),平台不因超大响应失败

#### Scenario: 凭证不出控制面

- GIVEN 配置了鉴权 headers 的 server
- WHEN 工具被调用
- THEN 出站调用由控制面发起并附解密 headers;agent 进程不存在
  该凭证;审计/日志无明文

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

### Requirement: 传输收敛

MCP 互操作 SHALL 仅实现 Streamable HTTP 传输(SSE 传输已被 MCP
官方废弃,不实现);仅实现 initialize/ping/tools/list/tools/call 方法
与 notifications 忽略语义;resources/prompts/sampling 等原语不在
本能力范围。

#### Scenario: SSE 形态响应解析

- GIVEN 外部 server 对 tools/call 返回 text/event-stream 形态
- WHEN 客户端解析
- THEN 按 SSE 帧聚合 data 行得到 JSON-RPC 响应(两种合法响应
  形态均可解)
