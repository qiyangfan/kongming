# 078-chat-stream-interaction-fixes 实施任务

前端单面变更,无后端与契约改动。web 侧暂无测试设施(066 未落地),
故可下沉的判定逻辑一律下沉为 `src/lib/**` 纯函数,由 066 的 vitest 批次接管断言。

## 1. 滚动跟随(近底才贴)

- [ ] 1.1 取对话区滚动容器引用:经 `ScrollArea` 拿 viewport 元素,**不改**
      `ui/scroll-area.tsx`(避免污染双仓标准件);确认判定不依赖 window
- [ ] 1.2 `src/lib/` 新增纯函数:由 `scrollTop/scrollHeight/clientHeight` 判定
      "是否处于底部附近"(阈值单一常量,禁散落的魔法数)
- [ ] 1.3 替换 `ChatPage.tsx:253-256` 的无条件 `scrollIntoView`:React 提交后再判定
      (双 rAF 或等价时机),近底才跟;非流式场景(切会话、新提交)仍强制贴底
- [ ] 1.4 非跟随时出现"回到底部"浮标;到达底部或用户主动回底后浮标消失
- [ ] 1.5 走查:流式期间上滚查看长结果表不再被拉回;375/1280 两档均验
      **验证**:`pnpm typecheck && pnpm lint` 绿

## 2. 渲染隔离(过程事件不重渲染历史项)

- [ ] 2.1 在途轮的过程状态从 `ChatPage` 页面级 state 下沉:首个事件插入占位一次,
      后续事件由 `StreamCard` 自持(消除每事件广播到整页)
- [ ] 2.2 历史消息项按数据内容 memo(比较器忽略回调身份);确认 result 终态
      接管路径与"快照退位"时序不变(与既有 `finalized` 机制等价)
- [ ] 2.3 复查引用稳定性:react-query 结构共享 + `toSyntheticMessage` 的字段构造
      在 memo 后仍成立(不因 memo 引入字段漏更新)
      **验证**:一轮问答内落定卡片渲染次数不再随事件线性增长(临时计数打印核验后删除)

## 3. 显式停止与终态回查

- [ ] 3.1 停止入口(禁用态与图标走 shadcn/ui + 既有 `lucide-react`,不手搓):
      中止 `AbortController` 并立即释放输入
- [ ] 3.2 文案与"断线续存"对齐:说明该轮仍在后台完成、可稍后回看;
      **不得**出现"已取消/已停止生成"类误导措辞(评审红线)
- [ ] 3.3 `src/lib/` 纯函数:是否需要对历史做回查(末条 USER 且无 ASSISTANT)+
      有界节奏(上限次数,超时即停)
- [ ] 3.4 接入回查:复用既有 `["messages", sid, tenant, ds]` 查询,不新增端点;
      终态出现或上限耗尽即停
      **验证**:中流刷新后应答自行出现;离开该路由不残留轮询(网络面板核验)

## 4. 收口

- [ ] 4.1 宪法 Article 8 自检 grep(无裸 `<button>`/`<input>`、无硬编码色值、
      无 inline margin/padding、无新增 `dark:` 覆盖)
- [ ] 4.2 亮暗两态 × 375/768/1280 三档走查改动页;动效遵循既有 `prefers-reduced-motion`
      处理口径
- [ ] 4.3 spec delta sync 进 `openspec/specs/query-streaming/` 与 `openspec/specs/web-nav/`
- [ ] 4.4 手工冒烟:正常问答、慢查询(selfheal 多轮)、中流停止、中流刷新、
      切会话、切租户/数据源六条路径;记录于 apply
- [ ] 4.5 `make check` 全量收口(确认后端与契约零漂移)

> 078 落地记录:
