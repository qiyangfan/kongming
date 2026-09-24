# 057-numeric-grounding 提案:派生量接地(禁止 LLM 心算最终数字)

## Why

问数主路径的事实数字来自 SQL 执行,可信;但 `summarize` 会写「环比增长约
50%」「A 占比 35%」这类**派生量**——当前由 LLM 在自然语言里心算。既有
018 只约束了「不外推到不可见数据」,管不住「从可见数字算错公式」。

幻觉形态实证类:

- 两行 2024=100、2023=80,模型写「增长 15%」(实为 25%);
- 三行占比写「约 40%」,与 `x/sum` 真值 33% 不符;
- `insights.type=trend` 编造「逐月上升」而结果只有两期。

风险等级高于闲聊产品:2C 问数用户会把「增长 50%」当结论引用。派生数字
错误是问数最贵、也最容易修的幻觉类型——**结论性数字必须可复算**。

## What Changes

- **数值白名单(硬约束)**:`summary` 与 `insights[].text` 中出现的数值
  MUST 来自:①结果集单元格值 ②服务端确定性 stats ③(ReAct 路径)计算
  工具返回值。三者皆无的数值 SHALL NOT 出现。
- **派生量禁心算**:增幅/占比/倍数/差值/复合变化率等结果集与 stats 中
  不存在的派生量,MUST NOT 由 LLM 口算写入;需要时:
  - ReAct/工具路径:调用 `compute_delta` / `compute_share`(服务端纯函数,
    入参显式数字,返回值进入白名单);
  - 固定图路径:服务端在 stats 中预计算常见派生量(同列环比、行间占比)
    **或**省略该表述——宁省略,不编造。
- **确定性数值审计(收口门)**:summarize 后对 summary/insights 做数值
  抽取,凡不在白名单的数值所在句 SHALL 剥离或降级为「详见表格」,不阻断
  主链路、不重试 LLM。
- prompt 同步:S 常量补「只写白名单数字;派生量不得心算」;insights 的
  trend/anomaly 类无白名单支撑时不得产出。
- 测试先行:白名单纯函数、审计剥离、compute 工具、prompt 断言、零回归。

## Non-goals

- **不做计算工件库 / contentHash 版本化跨轮复用**:SQL 重跑比缓存工件更
  符合问数语义;样本的工件框架不移植。
- **不做通用表达式计算器 / 符号数学引擎**:只做 delta/share 两类高频
  派生,其余派生宁省略。
- **不改 SQL 生成层的 ROUND 口径**(018 已有);不改 stats 现有字段语义
  (只增不改)。
- **不做图表轴刻度数值审计**(图表层另评);本变更只管 summary/insights
  文本。
- **不引入图内 LLM 判官 / 评分环**(已否决:伤流式体验)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `nl2sql-agent`:结果智能分析 SHALL 具备派生量接地——summary/insights
  数值白名单、派生量禁心算(工具或预计算或省略)、确定性数值审计剥离、
  零回归。

## Impact

- `apps/agent`:summarize 收口审计纯函数、stats 预计算扩展、
  `compute_delta`/`compute_share` 注册进内核工具表(与 execute_sql
  同注册哲学,只读副作用=false);prompts.py 规则增量(资产纪律登记)。
- 无 schema 变更(ADR-004 无涉);compute 工具为 Agent 内核工具,经
  控制面工具回调面时复用既有 tool 执行通道,无新凭证(ADR-002 无涉)。
- 契约(ADR-005):QueryOut 字段结构零改动(insights/stats 已有);若
  compute 工具调用出现在 tool_call 流事件,属既有工具流契约,无新事件型。
- apps/web 零改动(展示层不动)。
