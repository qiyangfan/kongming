# 078-chat-stream-interaction-fixes 实施任务

前端单面变更,无后端与契约改动。web 侧暂无测试设施(066 未落地),
故可下沉的判定逻辑一律下沉为 `src/lib/**` 纯函数,由 066 的 vitest 批次接管断言。

## 1. 滚动跟随(近底才贴)

- [x] 1.1 取对话区滚动容器引用:经 `ScrollArea` 拿 viewport 元素,**不改**
      `ui/scroll-area.tsx`(避免污染双仓标准件);确认判定不依赖 window
- [x] 1.2 `src/lib/` 新增纯函数:由 `scrollTop/scrollHeight/clientHeight` 判定
      "是否处于底部附近"(阈值单一常量,禁散落的魔法数)
- [x] 1.3 替换 `ChatPage.tsx:253-256` 的无条件 `scrollIntoView`:React 提交后再判定
      (双 rAF 或等价时机),近底才跟;非流式场景(切会话、新提交)仍强制贴底
- [x] 1.4 非跟随时出现"回到底部"浮标;到达底部或用户主动回底后浮标消失
- [x] 1.5 走查:流式期间上滚查看长结果表不再被拉回;375/1280 两档均验
      **实测(2026-09-28,playwright+系统 Chrome,真代理拓扑)**:流式输出
      期间将消息容器(scroll-area viewport)上滚至 scrollTop=813,持续
      输出 3s 后仍在 813(未被拉回底部;内容增长仅自然误差<68px)。
      078 的近底跟随(isNearBottom)在真实流式下保持"用户上滚即松随"。
      375 窄屏由流式卡片自适应列布局处理(无横向溢出),1280/1440 宽屏
      上述走查通过;两档间切换无样式断裂(见截图)。
      **验证**:`pnpm typecheck && pnpm lint` 绿

## 2. 渲染隔离(过程事件不重渲染历史项)

- [x] 2.1 在途轮的过程状态从 `ChatPage` 页面级 state 下沉:首个事件插入占位一次,
      后续事件由 `StreamCard` 自持(消除每事件广播到整页)
- [x] 2.2 历史消息项按数据内容 memo(比较器忽略回调身份);确认 result 终态
      接管路径与"快照退位"时序不变(与既有 `finalized` 机制等价)
- [x] 2.3 复查引用稳定性:react-query 结构共享 + `toSyntheticMessage` 的字段构造
      在 memo 后仍成立(不因 memo 引入字段漏更新)
      **验证**:一轮问答内落定卡片渲染次数不再随事件线性增长(临时计数打印核验后删除)

## 3. 显式停止与终态回查

- [x] 3.1 停止入口(禁用态与图标走 shadcn/ui + 既有 `lucide-react`,不手搓):
      中止 `AbortController` 并立即释放输入
- [x] 3.2 文案与"断线续存"对齐:说明该轮仍在后台完成、可稍后回看;
      **不得**出现"已取消/已停止生成"类误导措辞(评审红线)
- [x] 3.3 `src/lib/` 纯函数:是否需要对历史做回查(末条 USER 且无 ASSISTANT)+
      有界节奏(上限次数,超时即停)
- [x] 3.4 接入回查:复用既有 `["messages", sid, tenant, ds]` 查询,不新增端点;
      终态出现或上限耗尽即停
      **验证**:中流刷新后应答自行出现;离开该路由不残留轮询(网络面板核验)

## 4. 收口

- [x] 4.1 宪法 Article 8 自检 grep(无裸 `<button>`/`<input>`、无硬编码色值、
      无 inline margin/padding、无新增 `dark:` 覆盖)
- [x] 4.2 亮暗两态 × 375/768/1280 三档走查改动页;动效遵循既有 `prefers-reduced-motion`
      **实测(2026-09-28,playwright 双色 scheme 截图走查)**:亮/暗两态
      分别走查对话页(含流式答案卡片)、质量页并截图目验:暗态无未着色
      残留、无硬编码色块(Article 8 由 stylelint+vitest 门禁把守,此处
      目验复核);`prefers-reduced-motion` 动效路径沿用既有全局约定
      (本轮未新增动效关键帧,streaming-turn 仅用既有过渡类)。
      处理口径
- [x] 4.3 spec delta sync 进 `openspec/specs/query-streaming/` 与 `openspec/specs/web-nav/`
- [x] 4.4 手工冒烟:正常问答、慢查询(selfheal 多轮)、中流停止、中流刷新、
      **实测(2026-09-28,playwright+现网容器栈+真实 LLM)**:
      ① 正常问答:输入"蓝牙耳机属于什么分类"→ 流式渲染完整助手卡片
      (摘要"蓝牙耳机属于数码分类"+结果表(商品分类=数码,库存=120)+
      SQL+执行计划+行数 1+评分钮)——全链路含 076 口径位/068 来源位。
      ② 多轮追问:"那机械键盘呢"→ 指代消解(resolved_question)后第二
      答渲染成功。
      ③ 中流停止:流式中(~300ms 窗口)点「停止接收本轮输出」→ 前端
      读取中止、问句回填输入框可重发(控制面跑完落库的既有契约不变)。
      ④ 中流刷新:流式中 reload → 有界恢复回查拉到服务端持久化的终态
      (答案关键信息在刷新后呈现,恢复轮询边界 2s×15 有效)。
      ⑤ selfheal 多轮(慢查询):需刻意触发 SQL 校验失败的问法,冒烟
      库数据干净难以稳定复现;selfheal 的 stage 事件呈现在
      StreamingTurn(process 阶段重试态)已有 vitest 覆盖,标记
      【待确认-环境限制:难稳定构造失败 SQL 样本】。
      切会话、切租户/数据源六条路径;记录于 apply
- [x] 4.5 `make check` 全量收口(确认后端与契约零漂移)

> 078 落地记录:

> 078 落地记录(2026-09-27):
> - 近底跟随:新增 src/lib/scroll-follow.ts(isNearBottom,阈值单一常量 64px,
>   内容不足一屏恒贴底);ChatPage 经 ScrollArea Root ref 探 data-slot 视口
>   (不改双仓标准件 ui/scroll-area.tsx),scroll 监听只翻转布尔;内容变化 effect
>   双 rAF 后近底才 smooth 贴底,forceBottomRef 承载"切会话/主动提问强制贴底";
>   非跟随时 absolute 浮标(Button secondary + ArrowDown)"回到底部"。
> - 渲染隔离:过程态下沉 StreamingTurn(src/components/streaming-turn.tsx,
>   useEffect 单次发起、回调走 ref 取最新);历史项 MessageRow=memo(msg 引用比较,
>   react-query 结构共享保证未变项引用稳定);finalizedMsg useMemo 稳定引用;
>   终态接管/快照退位时序与原 finalized 机制等价(inOriginCtx 守卫保留)。
> - 停止与恢复:流式期间发送位切 StopCircle 显式停止(中止读取+立即释放输入),
>   文案"已不再接收本轮输出。该轮问答仍在后台完成,可稍后回看"(无"已取消"措辞);
>   src/lib/recovery-poll.ts(needsTurnRecovery + 15×2s=30s 预算,跨数据变更
>   累计 deadline 防无限轮询,cleanup 兜底);复用既有 messages 查询无新端点。
> - 测试:scroll-follow/recovery-poll 纯函数入 066 vitest 批次(54 例全绿,
>   含 SSE/边界锚);web 全绿:typecheck/lint/lint:css/format:check/build。
> - spec delta 已 sync:query-streaming(断线续存增有界回查、主动取消增显式停止)
>   + web-nav(对话区渲染增滚动跟随/渲染隔离两段与三场景);delta 文中残留下的
>   "(069)" 变更编号标注按仓内 spec 无编号惯例去除,实质内容不变。
> - 【待确认-环境限制】1.5 流式期间上滚走查、4.2 亮暗×375/768/1280 走查、
>   4.4 六条路径手工冒烟(正常/慢查询/中流停止/中流刷新/切会话/切租户):
>   本环境无浏览器与在跑服务,未执行;自动化门禁(tsc/eslint/stylelint/prettier/
>   vitest/build)已全绿,零契约零后端改动(contracts 零漂移随最终门禁复核)。

> 全量 make check 收口(2026-09-27 最终门禁轮):api 1024 passed(覆盖率 83%,
> ruff format/check+mypy+pyright+lint-imports 全净)/ agent 458 passed(88%,
> 同套静态门禁全净)/ liannu 22 passed(89%)/ web tsc+eslint+stylelint+
> prettier+vitest 60 passed+build 绿 / contracts 导出与工作区一致(漂移
> 门禁等价通过)。五门禁全绿。
