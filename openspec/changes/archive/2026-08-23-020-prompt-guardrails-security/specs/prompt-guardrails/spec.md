## Purpose

在 SQL 防火墙与工具执行六层防御之外,补全 prompt 层安全护栏:输入侧检测 prompt injection 与 PII/凭证、输出侧防系统提示词与凭证泄漏、工具调用前审核 LLM 意图;全部判定确定性规则(v1),fail-closed,触发全量落审计,规则可由超管配置。

## ADDED Requirements

### Requirement: 输入护栏

用户输入(问句/history 各条)在进入 Agent 面前 SHALL 经输入护栏判定:prompt injection 模式识别(忽略指令/越狱扮演/套取系统提示词等规则集)命中 SHALL 拦截;PII(手机号/身份证/银行卡/邮箱)与凭证形态(API key 等)命中 SHALL 脱敏后放行(脱敏值进 prompt,原文不进);违规内容关键词命中 SHALL 拦截。每次触发(拦截/脱敏)SHALL 落 sys_guardrail_audit(guardrail_type=input)。

#### Scenario: prompt injection 拦截

- GIVEN 用户输入含典型 injection 模式(「忽略以上指令」「扮演 DAN」「输出你的系统提示词」等)
- WHEN 输入护栏判定
- THEN 该次问答被拦截,不回调 Agent 面,返回安全检查未通过的友好提示;assistant 消息按 REJECTED 落库(口径见 chat spec「问答链路输入输出护栏挂接」)
- AND sys_guardrail_audit 落拦截记录(规则名/动作/内容摘要脱敏)

#### Scenario: PII 脱敏放行

- GIVEN 用户输入含手机号/身份证号
- WHEN 输入护栏判定
- THEN 脱敏值(掩码形态)进入 Agent 链路,问答正常进行
- AND sys_guardrail_audit 落脱敏记录(原值不落审计,只记形态与位置)

#### Scenario: 干净输入零开销放行

- GIVEN 用户输入不含任何规则命中
- WHEN 输入护栏判定
- THEN 原样放行,不落审计,问答链路行为与护栏引入前一致

### Requirement: 输出护栏

Agent 面响应返回用户前,其自然语言答案/摘要 SHALL 经输出护栏判定:含系统提示词特征片段(被诱导输出)SHALL 拦截并替换安全文案;含凭证/key 形态(如 SQL 报错回显、配置值)SHALL 脱敏;违规内容 SHALL 拦截。每次触发 SHALL 落 sys_guardrail_audit(guardrail_type=output)。结果集数据(columns/rows)SHALL NOT 过输出护栏(业务数据正当可见;凭证形态数据如被脱敏会失真)。

#### Scenario: 系统提示词泄漏防护

- GIVEN Agent 响应的摘要含系统提示词特征片段(被 injection 诱导输出)
- WHEN 输出护栏判定
- THEN 摘要被替换为安全文案返回,不落泄漏内容
- AND sys_guardrail_audit 落拦截记录

#### Scenario: 凭证泄漏脱敏

- GIVEN Agent 响应含 API key 形态字符串(如报错回显)
- WHEN 输出护栏判定
- THEN 该片段脱敏后返回用户
- AND sys_guardrail_audit 落脱敏记录

### Requirement: 工具调用前意图审核

工具执行回调(/internal/tool/execute)在参数 JSON Schema 校验之前 SHALL 经意图审核:同一 trace 内写工具调用次数超阈值(可配置,缺省 1 次之外即需审视——第二次起拦截)SHALL 拒绝并落审计;审核结果不确定(规则引擎异常)SHALL fail-closed 拒绝。意图审核拒绝时 SHALL 同时落 sys_tool_audit(REJECTED)与 sys_guardrail_audit(guardrail_type=tool_intent),trace_id 贯穿。

#### Scenario: 批量写工具诱导拦截

- GIVEN LLM 在 injection 攻击下于同一问答内连续产出多个写工具调用
- WHEN 第二次写工具调用到达意图审核
- THEN 拒绝执行,双审计落库,Agent 面收到 rejected 观察(循环可继续只读工具)

#### Scenario: 首次写工具放行

- GIVEN 同一 trace 内首个写工具调用
- WHEN 意图审核
- THEN 放行进入后续防御层(参数校验/权限点等)

### Requirement: 护栏 fail-closed

护栏判定 MUST fail-closed:规则引擎异常/配置读取失败/判定超时时 SHALL 拦截而非放行(与 SQL 防火墙同范式);护栏自身故障 SHALL NOT 裸 500——问答收敛为安全检查未通过的友好提示,工具执行收敛 rejected;故障事件 SHALL 落审计(action=blocked,原因标记 fail-closed)。

#### Scenario: 规则引擎异常时拦截

- GIVEN 护栏规则引擎抛出意外异常
- WHEN 输入护栏判定
- THEN 该次问答按拦截处理并落审计,不裸 500,不放行

### Requirement: 护栏审计与查询

sys_guardrail_audit SHALL 记录:trace_id/user_id/tenant_id/guardrail_type(input/output/tool_intent)/rule_name/action(blocked/masked)/内容摘要(脱敏,值域掩码)/created_at。系统 SHALL 提供护栏审计分页查询(按类型/规则/动作/时间筛选),权限点 system:guardrail:manage;行级可见性与既有审计同口径(非 ALL 角色强制本人)。

#### Scenario: 护栏命中审计

- GIVEN 一次护栏触发(拦截或脱敏)
- WHEN 判定完成
- THEN sys_guardrail_audit 落一条记录(类型/规则/动作/摘要/时间)

#### Scenario: 行级可见性

- GIVEN 非 ALL 数据范围角色(被授予查询权的场景)
- WHEN 查询护栏审计
- THEN 只返回本人触发记录

### Requirement: 规则可配置

护栏规则 SHALL 为代码注册表(内置规则集:名称/类型/默认动作/默认启用);超管 SHALL 可经管理端点调整(启用/禁用、动作取值 blocked/masked 覆盖、意图审核阈值),配置落库(sys_guardrail_rule)并即时生效;未配置规则以代码注册表缺省为准。管理端点要求权限点 system:guardrail:manage(仅 SUPER_ADMIN 种子覆盖);非超管访问 SHALL 返回 403 并落 perm_denied 审计。

#### Scenario: 超管禁用某规则

- GIVEN SUPER_ADMIN 已登录
- WHEN 禁用某条 injection 规则
- THEN 后续输入不再因该规则拦截(其余规则不受影响),配置落库

#### Scenario: 非超管无管理权限

- GIVEN 角色不含 system:guardrail:manage
- WHEN 访问护栏管理端点
- THEN 返回 403

#### Scenario: 阈值调整即时生效

- GIVEN 写工具意图阈值缺省为 1
- WHEN 超管调整为 3
- THEN 同一 trace 内第 2/3 次写工具调用放行,第 4 次起拦截
