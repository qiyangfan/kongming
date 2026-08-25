## ADDED Requirements

### Requirement: 问答链路输入输出护栏挂接

问答查询端点(同步与流式)的用户输入 SHALL 在回调 Agent 面前过输入护栏(prompt-guardrails spec):拦截时落 REJECTED 消息(安全检查未通过文案,复用既有 REJECTED 分态)并返回 success=false,不回调 Agent 面;脱敏时脱敏值进链路(USER 消息落库与审计记原始问句的口径不变——审计 sys_sql_audit 不受影响,护栏事件落 sys_guardrail_audit)。Agent 面返回的自然语言答案/摘要 SHALL 在落库与返回前过输出护栏;输出护栏拦截时消息 status=SUCCESS(查询本身成功),摘要替换为安全文案。护栏故障 SHALL fail-closed(输入按拦截、输出按替换),不裸 500。

#### Scenario: 输入拦截落 REJECTED

- GIVEN 用户问句命中 injection 规则
- WHEN 提交问答
- THEN 响应 success=false、assistant 消息 status=REJECTED、文案为安全检查未通过
- AND 不回调 Agent 面,sys_sql_audit 无记录,sys_guardrail_audit 落拦截记录

#### Scenario: 输入脱敏正常问答

- GIVEN 用户问句含手机号
- WHEN 提交问答
- THEN 问答正常进行(Agent 收到脱敏问句),sys_guardrail_audit 落脱敏记录

#### Scenario: 输出泄漏拦截

- GIVEN Agent 返回的摘要含系统提示词特征片段
- WHEN 响应组装
- THEN 摘要替换为安全文案,消息 SUCCESS(结果集照常),sys_guardrail_audit 落拦截记录

#### Scenario: 护栏关闭项零行为变化

- GIVEN 全部护栏规则被禁用(或无任何命中)
- WHEN 提交问答
- THEN 链路行为与护栏引入前逐字段一致(零回归)
