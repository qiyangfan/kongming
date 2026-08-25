# 028 mcp-interop 技术设计

## Context

019 工具链是静态注册表+六层防御;025 Bot 有 api-key 体系与渠道执行。
MCP 互操作需要:出站(动态工具桥接)与入站(Bot 暴露)双向,全程
零新依赖、agent 零出站(ADR-002)。

## Decisions

### D1: 传输与协议面(手写 JSON-RPC,零新依赖)

仅 Streamable HTTP:POST 单端点,请求 `Accept: application/json,
text/event-stream`;响应两种形态都解——`application/json` 直读;
`text/event-stream` 按 SSE 帧解析(data: 行聚合,id: 匹配 JSON-RPC id;
017 web 侧分帧经验的 Python 版,迷你解析 ~40 行)。方法子集:
initialize/ping/tools/list/tools/call(+notifications/* 返回 202 空)。
协议版本协商:initialize 请求带 "2025-03-26",响应版本原样回显
(不支持的版本由 server 侧拒绝,原样透传错误)。

不引官方 mcp SDK 的理由:技术栈锁定(零新依赖纪律);本变更只用
4 方法请求/响应形态,SDK 的会话管理/能力协商大部分用不上;手写面
~200 行可控且测试全覆。

### D2: sys_mcp_server(0016)

id uuid7 / tenant_id / server_key(pattern ^[a-z][a-z0-9_]{1,63}$,租户内
唯一)/ name / endpoint(varchar 512,http/https 传输层校验)/
headers_cipher(AES-GCM 密文,008 cipher 复用;明文只在写入与出站
调用瞬时存在)/ enabled bool / tools JSONB 快照([{name, description,
schema, read_only}])/ last_sync_at / last_sync_error(varchar 500)/
created_at/updated_at。CHECK:server_key 由传输层 pattern+领域层
fullmatch(008 R1 教训:防尾换行绕过)。

查询接口 headers 一律掩码:返回 [{key, value:"***"}](键名可见,
值永不回显——编辑态保留语义:未改动的键不重传,值留空=保持)。

### D3: 动态注册与解析

快照即注册(不建独立工具表):server enabled 时 tools 快照展开为
ToolSpec(name=mcp__<key>__<tool>)。同步流程:initialize→tools/list→
逐工具组装(同名冲突:先注册方胜,后者跳过并记入 last_sync_error
告警段)→ 整快照原子替换。server 禁用/删除 → 下次解析自然消失
(无残留清理面)。

toolcall 主链接入:execute_tool 的 get_tool(静态)未命中 → 查
DynamicToolResolver(新端口,mcp.repository 实现:按 tenant 查
enabled server 快照,按全名匹配)。命中动态工具的语义:perm_code=
"tool:mcp:use",write=not read_only(fail-closed),require_confirm=
write(未标注工具走 019 确认流,确认重放经同一 dispatch)。L4
JSON Schema 用快照 schema;L5 限流共用;审计 tool_name 含 server
溯源(mcp__<key>__ 前缀)。

### D4: 出站调用(mcp.client)

McpClient(端口在 mcp/ports.py,实现 mcp/client.py 基于 httpx):
call(server_row, tool_name, args) → 解密 headers 组装请求 →
POST endpoint(JSON-RPC tools/call)→ 超时 15s(配置)→ 响应
>1MB 截断(附 truncated 标记,审计记原始大小)→ 结构化错误
(timeout/connrefused/协议错 → 统一 McpCallError 分类回灌 agent
可自愈改道)。出站凭证不落日志/审计(异常路径只记状态码/文案)。

### D5: /mcp 入站

POST /mcp:Authorization Bearer(025 OpenAI 端点同款位置)取
api-key → 025 resolve_api_key(哈希查行+撤销检查)→ 得
tenant_id/scopes(bot_keys)。tools/list:启用 Bot 过滤(租户∧
scopes 交集)→ [{name: bot__<botKey>, description, inputSchema:
{type:object, properties:{question:{type:string}}, required:[question]}}]。
tools/call:name 解析 botKey →  scopes 复校(fail-closed)→ 025
service 以创建者代理身份执行(channel="mcp",输入护栏/配额/审计
同 web 口径)→ 结果文本包 MCP content[{type:text}]。Bot 的
is_public=false 不影响 /mcp(渠道语义不同:api-key 即授权)。

initialize 响应:{protocolVersion 回显, capabilities:{tools:{}},
serverInfo:{name:"kongming", version}}。未知方法 → JSON-RPC
-32601;无效 key → 401(非 JSON-RPC 错误,HTTP 层拒)。

### D6: 目录合并(web 可见性)

- GET /system/tools(019 目录,system:tool:manage):静态 REGISTRY
  + 当前租户启用 server 的 MCP 工具(纯增 server_key 溯源字段)。
- Bot 编辑器目录(025 tools_for_perms):持 tool:mcp:use 者追加
  mcp__ 前缀通配(函数返回集合无法枚举动态名——改契约:返回
  (names, allow_mcp_prefix) 或调用方另查;取最小侵入:
  tools_for_perms 增形参 mcp_names: Collection[str] = (),由
  bot service 组合根查动态名传入;025 既有调用点逐字兼容)。
- Bot 绑定 MCP 工具的随 server 禁用而失效:运行时动态解析查不到
  → TOOL_NOT_REGISTERED 拒绝落审计(spec scenario 字面)。

### D7: 测试策略

- 纯函数:SSE 帧解析/JSON-RPC 组装/掩码/命名与冲突/快照展开。
- 假 MCP server(httpx ASGI 直连本测试进程 FastAPI 子应用):
  initialize/tools/list/tools/call 三方法,供发现与执行链路 HTTP 级
  直证(无外部网络)。
- 安全矩阵(测试先行):无 system:mcp:manage 403/跨租户 404 同形/
  headers 密文落库+掩码回显/tool:mcp:use 未授拒/未标注工具
  PENDING_CONFIRM+确认后真执行/1MB 截断/超时分类/key 撤销 401/
  跨租户 Bot 不可见。
- /mcp 入站:initialize→tools/list→tools/call 全链(真 Bot 定义,
  agent fake)。

## Risks / Trade-offs

- [外部 server 是攻击面] → 出站全在控制面(凭证不过 agent);超时+
  大小硬上限;写 fail-closed 确认;审计全量。恶意 server 返回的
  prompt 注入内容经 020 输出护栏(工具结果回灌前不过护栏——
  018/020 护栏作用于用户输入与最终输出;工具结果注入是已知
  残余面,与内置 rag 内容同风险级,挂账)。
- [快照与实况漂移] → 手动同步+保存即同步;漂移期调用旧工具名
  被外部 server 拒→结构化错误回灌(可接受)。
- [/mcp 无会话态] → 部分客户端强依赖 Mcp-Session-Id;v1 返回
  无会话头,主流客户端(Claude Desktop/Cursor)兼容无态形态
  (实测冒烟)。

## Migration Plan

0016 建新表;权限点种子只增(20/21);存量零影响;回滚=删端点
与注册面(纯增无耦合)。

## Open Questions

- O1:工具结果 prompt 注入护栏(工具返回内容的输出护栏挂接面)——
  随 020 域深化。
- O2:MCP resources/prompts 原语;O3:定时自动重同步(现为手动+
  保存触发);O4:JWKS/OAuth 入站(SSO 域)。
