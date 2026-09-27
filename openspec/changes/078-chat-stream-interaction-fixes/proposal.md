# 078-chat-stream-interaction-fixes 提案:对话页流式交互三项缺陷

## Why

对话页在流式链路上有三处**用户可直接感知**的缺陷。逐条以代码为证:

### 1. 自动贴底覆盖用户滚动

`ChatPage.tsx:253-256` 无条件执行 `bottomRef.current?.scrollIntoView(...)`,
依赖数组含 `streaming`,而 `streaming` 每个流事件都是新对象——一轮问答约 14–20 个事件,
即**每事件把视图强拽回底部一次**,持续整个轮次。

后果:用户在本轮结果表(最多 1000 行,卡片内横滚)里往上翻、或想回看上一条回答时,
会被反复拽回去。这不是美观问题,是"结果给了但读不完"。

### 2. 每个流事件重渲染整条消息列表

`apps/web/src` 全仓 `React.memo` **零命中**;消息列表内联在 `ChatPage` 的 return 里
(`:429-440`),`streaming` 是页面级 state。于是每个流事件都重新渲染**全部已挂载的
`AssistantCard`**——每张含结果表格、图表卡、导出按钮、反馈行。

现状不是逐 token(本仓 summary 是单帧完整下发),所以不至于每 token 一次;
但 stage / tool_call 事件一轮十几到几十次,长会话 + 大结果表下每次全列表 reconcile
的开销是可感知的。图表这一侧已经做对了(`chart-view` 用 fingerprint 派生、
`option` 走 useMemo、react-query 结构共享保持引用稳定),**恰好证明其余部分没做**——
同一页面里两种成熟度并存。

### 3. 没有停止入口

发送键在流式期间只是变成 `disabled`(`ChatPage.tsx:580-591`),`AbortController` 仅在
切会话 / 切租户 / 切数据源 / 卸载时被触发(`:223-242`)。用户面对一轮慢查询
(`statement_timeout` 30s,叠加 selfheal 可到数分钟)**唯一能做的是切走或刷新页面**。

**语义必须先定清楚,否则文案会撒谎**:既有契约
`query-streaming/spec.md:111-115` 明文「主动取消不等同于后端中止」——前端中止连接后,
控制面仍继续消费 Agent 流至终态并照常落库。因此本变更的按钮语义是
**「不再接收」而非「取消执行」**;要做真取消需要 api↔agent 新增中止协议(涉 ADR-002
交互面与 `nl2sql-agent` 契约),属独立变更,不在本次范围。

### 4. 顺带同源的一处:刷新后本轮"消失"

中流刷新后,该轮在服务端跑完并落库,但 `["messages", sessionId, tenant, ds]` 查询
无轮询、无挂载后主动回查,用户看到的是"我问了但没人答",必须切走再回来才出现。
与第 3 项同源(都是"前端脱离了流但没找回终态"),一并处理。

## What Changes

1. **贴底改为"近底才跟随"**:读 `ScrollArea` 的 viewport 元素(Radix 隐藏原生滚动条,
   判定不能挂在 window 上),在 React 提交后判定距底距离,超过阈值即**不再自动滚动**;
   出现「有新内容」浮标供用户主动回底。切换会话、用户主动提问仍强制贴底。
2. **流式状态隔离**:在途轮的占位只在首个事件插入一次,后续事件写入在途卡自持的状态,
   不再经页面级 state 广播;已落定的历史消息项按数据字段 memo(忽略回调身份)。
   只用 React 19 与既有 react-query 能力,**不引入虚拟列表、不引入新状态库**。
3. **停止接收控件**:流式期间提供显式停止;点击后中止读取、按"本轮已在后台完成,
   可稍后回看"口径呈现,问句可立即重发。不得暗示服务端已中止执行。
4. **有界恢复轮询**:进入会话时若该会话末条为 USER 且其后无 ASSISTANT,
   以有界间隔回查历史直至该轮终态出现或超时(超时即停,不无限轮询)。
   依据:控制面对每条链路都保证写终态行(含 agent 流无终态的汇聚分支),
   因此"末条是 USER"必然意味着有一轮正在服务端收尾。
5. 前端宪法收尾:`pnpm typecheck && pnpm lint` + Article 8 自检 + 375/768/1280 × 亮暗走查。

## Non-goals

- **不做逐 token 流式**:`query-streaming/spec.md:65-67` 明文"逐段 token 流为远期演进,
  不在本契约内";要做需同时改 agent 出站与 api 中继,是另一个变更。
- **不做服务端真取消**(新增中止协议、改 agent 契约)——见 Why 第 3 点的语义区分。
- **不做断线续传 / `Last-Event-ID` 重连**:契约明文「断线后 SHALL NOT 重发、不补偿」。
- **不引入虚拟滚动、react-window、新状态管理库、toast 体系**:现有列表规模不需要虚拟滚动,
  第 2 项用 memo + 状态隔离即可解决;toast 与本仓全站一致的 inline `role="alert"` 相反。
- **不做前端组件测试 / jsdom / E2E**:066 明文非目标;本变更涉及的纯逻辑
  (近底判定阈值、事件合并)若可下沉到 `src/lib/**`,归 066 的 vitest 批次覆盖。
- **不改任何后端行为、不改响应信封**(ADR-004/ADR-005 零触发)。

## Capabilities

### Modified Capabilities

- query-streaming:前端主动取消扩展为**提供显式停止入口且语义与"后端不中止"一致**;
  断线续存扩展出**末条为 USER 时的有界恢复回查**。
- web-nav:对话区渲染补入**近底才跟随的滚动策略**与**流式期间历史项不重复渲染**两项约束。

## Impact

- **落点**:仅 `apps/web`——`pages/ChatPage.tsx`(滚动 effect、streaming 状态下沉、
  停止与恢复)、`components/stream-card.tsx`(在途卡自持状态)、
  `components/assistant-card.tsx`(memo 边界)、`components/ui/scroll-area.tsx`
  消费侧取 viewport 引用(不改组件本身)。
- **无 OpenAPI / 契约变更**;`contracts/openapi.json` 零漂移。
- **双仓标准件纪律**:若为取 viewport 而需改 `ui/scroll-area.tsx`,属本仓侧改动,
  按 AGENTS.md 的先改本仓再 `bash ../scripts/sync-frontend-standard.sh` 流程;
  优先做法是不改组件、经 `ScrollArea` 的 ref 取 viewport(避免污染标准件)。
- **无新依赖**(tech-stack 锁栈无涉)。
