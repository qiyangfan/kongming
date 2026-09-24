# 058-react-todo-honesty 设计

## Context

- ReAct 现状:`react_llm → tool_dispatch → finalize`,state 无目标账本;
  自愈/失败信息主要靠 messages 回灌。
- understand 已能拆多子问题(多轮指代与并列问法);ReAct 对多子问题靠
  模型自觉,无状态约束。
- 已否决:图内 LLM 判官、目标所有权框架、写操作动作记忆。

## Goals / Non-Goals

**Goals:** 多步运行可审计的完成度;说完成必须有证据。

**Non-Goals:** 见 proposal。

## Decisions

1. **todos 是 run 内数据结构,不是产品功能**  
   只存在于 Agent state 与 finalize 文本约束;不进 OpenAPI、不进 SSE
   新事件型。透出计划面板是后续 UX 提案。

2. **证据 = 工具成功结果引用**  
   DONE 的 evidence 指向本轮 tool 调用标识(或确定性节点产物,如
   execute_sql 的结果摘要哈希)。**模型自述不算证据**——状态迁移只能由
   tool_dispatch 回写触发。

3. **状态机最小集**  
   `PENDING → DONE` (工具成功) | `FAILED` (工具失败/拒绝) | `SKIPPED`
   (能力不适用/空结果且策略允许放弃)。禁止 PENDING→DONE 无证据边;
   禁止预算退出钩子把 PENDING 批量改 DONE。

4. **建立策略(保守)**  
   - understand 拆出 ≥2 子问题 → 每子问题一条 todo;  
   - 模型规划步输出 `plan` 工具/结构化块(可选)→ 按其目标建单;  
   - 否则不建单(单目标零回归)。  
   *替代方案*:每问强制建单——拒绝:简单问增加噪声与延迟,违背
   「零回归」。

5. **finalize 诚实模板**  
   存在非 DONE:固定披露未完成清单(goal + reason),禁用「全部/均已/
   已完成所有」类全称措辞(领域层字符串断言);全 DONE:允许正常收口。
   预算退出文案固定为「部分完成」语义。

6. **纯函数核心**  
   `apply_tool_result(todos, call) -> todos`、`assert_finalize_honest(todos, text)`
   放 models 层,可脱离容器单测(宪法 Article II)。

Schema 核对:无表(ADR-004 无涉)。

## Risks / Trade-offs

- [模型漏建单导致无账本] → 单目标本就无账本;多子问题靠 understand
  建单兜底,漏网时退化为现状,不更差。
- [诚实措辞断言过严误杀] → 断言只拦「全称完成」与「非 DONE 目标被称
  已完成」;不做人造 NLP。
- [finalize 文案变啰嗦] → 仅存在未完成项时展开清单;全 DONE 零变化。

## Migration Plan

行为开关可先 shadow(只记录不改写文案)一个版本再切诚实收口;无迁移。
回滚 = finalize 跳过断言。

## Open Questions

(无——计划可视化、跨会话 todos 已在 Non-goals。)
