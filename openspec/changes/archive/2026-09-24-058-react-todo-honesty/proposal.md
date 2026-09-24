# 058-react-todo-honesty 提案:多步 Todo 完成诚实(ReAct)

## Why

ReAct 内核在多子问题/多工具链上没有**完成度账本**:目标只活在提示词里。
链路一长(对比 A/B → 再算环比 → 再查维度)会出现两类典型不诚实:

1. **总结吹完成**——某子问题工具失败/空结果/能力不适用,finalize 仍写
   「以上已全部回答」;
2. **预算退出谎称完成**——步数/Token 耗尽后硬收尾,掩盖未做部分。

样本用「目标所有权」包装此事,本质就是 **todo list + 两条硬规则**:
`done` 必须挂证据;预算/失败不得标 done。本变更只做这个数据结构与诚实
收口,不引入 supervisor 框架、不做计划 DSL。

## What Changes

- ReAct run state 增加 `todos[]`:id、goal、status
  (PENDING/DONE/FAILED/SKIPPED)、evidence(工具调用标识或结果引用)、
  reason(失败/跳过时)。
- **建立**:多子目标请求(understand 拆出多子问题,或模型在规划步显式
  建单)时落 todos;单目标简单问可不建单(零回归)。
- **完成规则(硬断言)**:
  - `DONE` 必须 evidence 非空且对应工具调用成功(有结果);
  - 工具返回空结果/能力不适用/校验拒绝 → `FAILED` 或 `SKIPPED` 并填
    reason,**不得** DONE;
  - 预算耗尽(步数/Token/超时)→ 未完成项保持 PENDING/FAILED,不得
    改写为 DONE。
- **finalize 诚实契约**:收口文本 MUST 逐项对齐 todos 状态——DONE 交付
  结论;FAILED/SKIPPED/PENDING 如实说明未完成或原因;存在非 DONE 时
  MUST NOT 声称「已全部完成/回答」。
- 测试先行:状态机纯函数与 finalize 诚实断言。

## Non-goals

- **不做**目标所有权框架 / 可修订 Plan DSL / 增量 DAG / supervisor 多
  Agent:那是样本重资产,2C 问数用 todo 账本足够。
- **不做写操作动作记忆 / 挂起指针 / double-texting**:当前无写工具,
  无消费者(有写工具时另立提案)。
- **不做跨会话 todos / 用户侧 Todo 编辑器**:仅本轮 run 内账本;是否透出
  流事件属后续 UX,本变更只保证文本收口诚实(契约零新增事件型)。
- **不改**单目标简单问的现有拓扑与响应字段(零回归)。
- **不做**自动重试未完成项(failed 不自动重排)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `tool-calling`:ReAct 循环 SHALL 维护多步 Todo 账本,完成必须有证据,
  预算/失败不得标完成,finalize 与账本一致(诚实收口)。

## Impact

- `apps/agent` `graph/react/`:state 增 todos、规划/工具回写、finalize
  对齐;领域纯函数(状态机+诚实校验)零框架依赖(宪法 Article II)。
- 无 schema 变更(ADR-004 无涉);QueryOut 字段结构零改动(ADR-005 契约
  无新字段,todos 仅内部;summary 文本内容受诚实约束属行为非契约)。
- 不动信任边界(ADR-002):无新回调、无新凭证。
- 控制面零改动。

## Capabilities 影响说明

单目标路径不建 todos,行为逐字一致;多子目标路径仅增加收口诚实性,不
改变工具集、防火墙与执行通道。
