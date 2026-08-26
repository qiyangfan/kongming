# tool-calling 规格增量:轮次触顶终态携带部分结果

## MODIFIED Requirements

### Requirement: ReAct 工具调用循环(Agent 面)

Agent 面 SHALL 提供 ReAct 内核端点(内部令牌认证,ADR-002):LLM 根据用户意图自主决定调用哪个工具、接收工具结果、决定继续调工具还是产出最终答案。系统 MUST 保留既有 NL2SQL 链路 `/query` 与 `/query/stream`(8 节点图)的对外契约与行为不变,作为「预设工具配方」向后兼容。ReAct 端点终态响应 SHALL 与既有问答端点逐字段同构(success/verdict/summary/insights/stats/columns/rows/sql/retry_count/error,单一定义防漂移)。循环 SHALL 设轮次上限(可配置,缺省不超过 10),达到上限 SHALL 强制终止并返回结构化错误(携带已达上限提示),禁止无限循环;触顶终态 SHALL 携带已产出的部分结果——早前轮次若有成功 execute_sql,其结果集(columns/rows)与 SQL SHALL 随终态一并返回(error 与部分结果并存,消费方以 error 优先呈现、部分结果作参考),无已产出结果时仅返回错误。同一轮问答内的多次工具执行回调 SHALL 共用同一 trace_id。ReAct 请求 SHALL 纯增可选 bot_system_prompt(Bot 定义的系统提示,025):非空时替换内核默认系统提示的助手身份段(内核工具纪律后缀必须保留拼接),空时形态与注入前逐字一致。

#### Scenario: 数据查询场景 LLM 自主编排工具

- GIVEN 用户提问「查询租金最高的 5 部影片」,且 SQL 校验/执行/RAG 检索工具均已注入
- WHEN Agent 面进入 ReAct 循环
- THEN LLM 自主选择工具组合(如 rag_retrieve → 生成 SQL → validate_sql → execute_sql),最终返回的响应字段与既有 NL2SQL 链路逐字段同构

#### Scenario: 老链路向后兼容零回归

- GIVEN 评估集(016)中任一既有问题
- WHEN 经既有 `/query` 链路处理
- THEN 响应与行为与 019 之前完全一致,评估通过率不低于既有基线

#### Scenario: ReAct 链路评估等价

- GIVEN 评估集中任一数据查询问题
- WHEN 经 ReAct 内核处理
- THEN 响应字段语义与老链路等价,评估通过率不低于既有基线口径

#### Scenario: 循环终止条件

- GIVEN LLM 已收集足够信息
- WHEN LLM 在某一轮不再产出 tool_calls 而是产出最终文本答案
- THEN ReAct 循环终止,向控制面返回最终响应

#### Scenario: 循环深度上限保护

- GIVEN LLM 连续产出 tool_calls 未收敛
- WHEN 工具调用轮次达到配置上限
- THEN 循环强制终止并返回结构化错误(携带已达上限提示),无进一步工具副作用

#### Scenario: 轮次触顶保留部分结果

- GIVEN ReAct 循环第 2 轮 execute_sql 已成功返回结果集,后续轮次仍未收敛直至触顶
- WHEN 轮次上限触发强制终止
- THEN 终态 error 携带已达上限提示,且 columns/rows/sql 为该成功轮次的实际结果(不丢弃),消费方呈现错误与部分结果并存

#### Scenario: Bot 系统提示注入(025)

- GIVEN 请求携带 bot_system_prompt「你是售后客服助手」
- WHEN ReAct 内核组装系统提示
- THEN LLM 系统提示以 Bot 提示为身份与场景段,内核工具使用/效率/终止纪律后缀完整保留

#### Scenario: 无 Bot 提示零回归

- GIVEN 请求不携带 bot_system_prompt
- WHEN 内核执行
- THEN 系统提示与 025 之前逐字一致
