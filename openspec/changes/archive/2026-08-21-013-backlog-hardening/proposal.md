# 013 挂账清理小修包(backlog-hardening)

## Why

009/010/011 三轮变更的评审观察挂账中,有 7 项已到期且互不依赖:3 项是用户
可见的行为微修(重命名校验顺序、temporal 轴标签可读性、删除确认 busy 态),
1 项是 spec 已立约但实现未兑现的缺口(rag_retrieve 非 JSON 裸抛),3 项是
架构/性能收口(chat.service 构造反转入 import-linter 契约、httpx 连接复用、
散点 tooltip 信息量)。单项都太小不值得独立变更,打包一次清理,避免挂账
长期积压腐化(010 教训:chat.service 间接 import sqlalchemy 的契约缺口
越拖越难收)。

## What Changes

1. **重命名校验顺序修正(api chat)**:PATCH 会话标题先 strip 再校验非空与
   200 上限——此前带空白 201 字 strip 后 ≤200 被误拒 422(010 挂账⑰)。
2. **temporal 轴标签本地化(web chart-view)**:line/bar 时间类目轴的 ISO
   原文标签(2005-07-05T00:00:00)渲染为本地日期格式(011 挂账⑤)。
3. **散点 tooltip 补数值(web chart-view)**:scatter 悬停提示补 x/y 数值
   呈现,与 bar/line 信息量对齐(011 挂账⑨)。
4. **删除确认 busy 禁用(web 会话列表)**:删除请求进行中「再想想」取消
   按钮禁用,消文案语义瑕疵(010 挂账⑤)。
5. **chat.service 构造反转(api,零行为变化)**:chat/ports 加 ChatRepo
   Protocol(含 commit/rollback),service 不再直接构造 ChatRepository、
   不再 import sqlalchemy;import-linter 契约补 chat 域 models/service
   零框架,与 system/datasource/rag 三域对齐(010 挂账③)。
6. **AgentRagClient httpx 复用(api rag,零行为变化)**:实例级复用
   AsyncClient,消每请求 TLS 握手(009 挂账⑭,500 表润色=500 次握手)。
7. **rag_retrieve 非 JSON 收敛(agent)**:200 但报文非 JSON 的网关形态
   按失败返回 None 回退内省,不再裸抛 JSONDecodeError——补齐 spec 既有
   立约「召回服务失败时回退实时内省」(009 挂账⑮)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `web-nav`: 「结果图表渲染」requirement 增补——temporal 类目轴标签本地
  日期格式化、散点 tooltip 呈现 x/y 数值;「会话侧栏交互」requirement
  增补——删除确认请求进行中取消操作不可用。

注:重命名 strip 后校验(chat)、rag_retrieve 失败回退(nl2sql-agent)两项
是**实现补齐既有 spec 立约**(chat 主 spec 已立约「去首尾空白后非空且不超
上限」,nl2sql-agent 主 spec 已立约「召回服务失败时回退实时内省」),spec
文本无需变更,故不产生 delta。

## Non-goals

- 不做 web 前端单测基座(vitest 引入是大决策,随体验深化单独立项)。
- 不做 query_history 治理 TTL/上限(009 挂账③,随 eval 域)。
- 不做 rag 列注释进润色、reindex 先润色后删写(随 feedback 域变更搭车)。
- 不做图表类型偏好记忆、pixelRatio 配置化、pie 高基数设卡(011 挂账②③④,
  维持原裁定)。
- 不动图表推断规则;除 temporal 截断(轴/工具提示/pie 切片名同源)与
  scatter tooltip 外的既有渲染行为不变;不动 ChatRepository 任何 SQL
  语义;不动 AgentRagClient 的缓存键与降级语义。
- 除上述 7 项外的其余挂账一律维持挂账状态,不在本变更范围。

## Impact

- **api**:`kongming/chat/ports.py`(新增 ChatRepo Protocol)、
  `kongming/chat/service.py`(构造反转 + strip 后校验)、
  `kongming/chat/router.py`(组合根构造 repo 注入)、
  `kongming/rag/agent_callback.py`(client 复用)、
  `pyproject.toml`(import-linter 契约补 chat)。
- **agent**:`kongming_agent/apiclient.py`(rag_retrieve 非 JSON 收敛)。
- **web**:`components/chart-view.tsx`(temporal 标签 + scatter tooltip)、
  会话删除确认对话框(busy 禁用)。
- **契约**:零端点变化;RenameIn.title 传输层 maxLength 200→2000(D4 必然
  产物——业务上限 200 不变、领域层 strip 后判定,传输层仅防离谱请求体),
  `make contracts` 产物已重生成(仅此一处 diff)。
- **依赖**:零新增。
- **ADR 引用**:本变更不涉信任边界/认证/schema/契约管线;chat.service
  构造反转遵循宪法 Article II 分层纪律与 ADR-002 既有约束。
