# 026 conversation-artifacts 设计

## Context

既有:result_json 落库形态({columns,rows,insights,stats,chart,error})+
回显读档(_to_message)+ 010 EXPLAIN 的 message_id 归属校验与
「服务端复检后执行」先例 + 019 六层防御。约束:零表结构变更(result_json
内部演进)、契约纯增、既有交互零改动。

## Goals / Non-Goals

Goals:artifacts 派生三卡 + sql 编辑再执行 + chart 调参 + document
markdown;三路径同源。Non-goals 见 proposal(html/svg/code 渲染器不做)。

## Decisions

### D1: 派生纯函数(chat/models.py)

derive_artifacts(sql, chart, summary, datasource_key) -> list[Artifact]:
确定性映射(无 LLM);Artifact=TypedDict{type, content, metadata}。
sql 卡 content=sql,metadata={datasource_key};chart 卡 content="",
metadata={chart 完整配置 type/x/y};document 卡 content=summary,
metadata={}。

### D2: 三路径同源挂接

_finalize_reply:result_json 与 ChatOutcome 组装时调用 derive_artifacts
(纯增 artifacts 键入 result_json + ChatOutcome.artifacts 字段);
_to_message 回显:读档 artifacts,缺失时按要素确定性重算(与 chart
重算兜底同款先例);流式 result 帧经 ChatOutcome 自然携带。

### D3: rerun 端点(六层防御复用)

POST /chat/artifacts/rerun {message_id, sql}:归属校验(find_session_owned
经消息 JOIN 会话,与 010 find_explain_target 同款)→ internal.service.
execute(user_id=当前用户,trace_id 新,datasource_key=会话快照,limit
1000)→ 结果落新 assistant 消息(复用 _finalize_reply 形态:构造
AgentReply 后走终态组装,自然含 artifacts 派生与 020 输出护栏)→
响应返回新消息。BLOCKED/ERROR 落 REJECTED/FAILED 消息(同语义)。

### D4: web 渲染器

assistant-card 升级:artifacts 非空时按类型渲染——sql 卡(textarea
可编辑+「重新执行」Button)+ chart 卡(调参抽屉:标题/轴名/类型
toggle,option 本地 useMemo 重算重绘)+ document 卡(零依赖 Markdown 子集渲染——Token 化→React 元素,不经
dangerouslySetInnerHTML,原始 HTML 按字面展示(spec 安全条款天然满足);
不引 react-markdown:技术栈锁定,proposal 依赖节未放行新库——实施期
核实「既有 markdown 渲染」失实后的裁定+复制按钮)。无 artifacts 回落既有形态。rerun 调 api 后
invalidate messages(新消息入列)。

### D5: 契约

ChatQueryOut/SessionMessageOut 纯增 artifacts: ArtifactOut[](契约模型
ArtifactOut{type, content, metadata});RerunIn/RerunOut 入 OpenAPI。

## Risks / Trade-offs

- [rerun 被当新问答入口绕过记忆/护栏输入] → rerun 是 SQL 直给(非自然
  语言),输入护栏不适用(SQL 由防火墙管);输出护栏覆盖;spec 已立约。
- [编辑 SQL 注入攻击面] → 六层防御全量(与问答同构),测试先行。
- [result_json 体积增长] → artifacts 为引用性派生(内容复用既有字段),
  增量小。

## Migration Plan

零迁移;旧消息回显重算兜底;滚动重启任意序(契约纯增);回滚=前端
读不到 artifacts 走既有形态。

## Open Questions

- O1:chart 调参的持久化(调参结果随消息保存偏好)——随 personalization。
- O2:document 卡编辑再生成——随 prompt 深化。
