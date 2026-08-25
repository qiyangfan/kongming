# 026 conversation-artifacts:对话产物类型化

## Why

当前 assistant 响应的要素(SQL/图表/答案文本)是平铺字段,用户不能对
产物做二次操作——改一个 SQL 条件要重新问一遍,图表只能在建议类型上
被动切换。本变更把响应要素升级为类型化可交互产物(SQL 可编辑再执行、
图表可调参重绘、答案为 document 卡),持久化随消息回显,对标 Claude
artifacts 的最小可用形态。

## What Changes

- **artifact 类型化抽象**:assistant 消息 result_json 纯增 artifacts
  数组(由控制面从既有要素确定性派生——sql 非空→{type:sql};chart.type
  ≠none→{type:chart};summary 非空→{type:document}),字段
  type/content/metadata;无产物时 artifacts=[] 零回归(既有卡片渲染
  逻辑不受影响的向后兼容)。
- **类型化渲染器**(web):sql 卡=可编辑(代码框)+一键再执行;
  chart 卡=调参面板(标题/轴名/类型,前端重绘不调 LLM);
  document 卡=Markdown 渲染+复制。
- **SQL 再执行端点**:POST /chat/artifacts/rerun(编辑后 SQL 经
  消息归属校验 + 六层防御全量[与 010 EXPLAIN 同范式:服务端复检后才
  执行]),结果作为新 assistant 消息落库(不覆盖原 artifact)。
- **持久化与隔离**:artifacts 随 result_json 落库,刷新/重开完整回显;
  会话归属校验(跨用户 404 同形)复用 chat 既有判定。
- **安全**:再执行 SQL 走防火墙+只读池(与问答同构);artifact 内容
  经 020 输出护栏(再执行响应过护栏)。

## Capabilities

### New Capabilities

- `conversation-artifacts`: artifact 抽象与派生、类型化渲染、SQL 再执行、
  持久化与隔离的行为契约。

### Modified Capabilities

- `chat`: 消息 result_json 纯增 artifacts 字段(响应与回显)。

## Non-goals

- 不做 html/svg 渲染器(无产出源;随 sandbox/HTML 产出具象再开);
  不做 code 卡运行(cloud-code-sandbox 域);不做 artifact 版本历史/
  对比;不做 document 卡再生成(LLM 重写随 prompt 深化)。
- 不做 ReAct/Bot 渠道的 artifact 增强(派生逻辑对全渠道同源生效,
  无额外形态)。
- 不改既有卡片渲染的既有交互(图表切换器/导出/EXPLAIN 等不动)。

## Impact

- **代码**:api chat 域 result_json 组装纯增 artifacts 派生纯函数+
  rerun 端点(service 编排复用 internal 执行);web assistant-card
  升级为 artifact 渲染器(sql 编辑器+chart 调参+document markdown);
  契约纯增。
- **契约**:ChatQueryOut/SessionMessageOut 纯增 artifacts +
  /chat/artifacts/rerun 端点入 OpenAPI。
- **依赖**:web 代码编辑用现有 textarea(SQL 短文本,不引 Monaco)。
- **ADR 引用**:ADR-005(契约);表结构零变化(result_json 内部演进)。
- **安全**:rerun 六层防御全量(测试先行:写语句/保留表/越权消息)。
