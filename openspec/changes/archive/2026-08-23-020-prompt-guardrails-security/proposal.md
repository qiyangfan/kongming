# 020 prompt-guardrails-security:安全向 prompt 护栏

## Why

019 放开 tool-calling 后,prompt 层防御成为刚需:用户输入可含 prompt
injection(诱导 LLM 在权限内「合法」作恶)、PII/凭证(不应进 LLM prompt);
LLM 输出可能泄漏系统提示词或凭证;ReAct 循环的 LLM 工具调用意图需要
前置审核(防被诱导批量调用写工具)。既有 SQL 防火墙管 SQL 层、019 六层
防御管工具执行层,本变更补全 prompt 输入/输出/意图三层护栏,与既有
防御同范式 fail-closed + 全量审计。上游 030 自定位为 tool-calling 伴生域,
018 已立 Non-goal 不重复登记(018=质量向护栏,本变更=安全向)。

## What Changes

- **输入护栏**(chat 问答入口,进 agent 前):prompt injection 模式检测
  (忽略指令/扮演越狱/套取系统提示词等规则集)→ 拦截(REJECTED 落审计);
  PII/凭证检测(手机号/身份证/银行卡/邮箱/API key 形态)→ 脱敏后放行
  (原文不落 prompt,审计记脱敏事件,值域掩码);违规内容过滤(v1 关键词
  规则集)→ 拦截。
- **输出护栏**(agent 响应返回用户前):系统提示词泄漏检测(特征片段比对)
  → 拦截并替换安全文案;凭证/key 泄漏检测(同 PII 规则集)→ 脱敏;
  违规内容 → 拦截。
- **工具调用意图审核**(tool/execute 的 JSON Schema 校验之前,019 预留
  挂接点):同 trace 内写工具调用频次阈值(防诱导批量)、敏感操作模式
  → 拦截或标记落审计。
- **fail-closed**:护栏判定不确定(规则引擎异常/超时)时拦截而非放行,
  与 SQL 防火墙同范式;护栏自身故障不裸 500,问答收敛 REJECTED 友好文案。
- **sys_guardrail_audit**(Alembic 0009):每次护栏触发(拦截/脱敏/告警)
  落库(guardrail_type/rule_name/action/内容摘要脱敏/trace 贯穿)。
- **规则可配置**:护栏规则=代码注册表(内置规则集)+ 库表配置覆盖
  (启用/禁用/动作阈值/白名单),管理端点 system:guardrail:manage
  (仅 SUPER_ADMIN 种子覆盖);审计查询沿用行级可见性口径(非 ALL 强制本人)。
- **web**:/admin/guardrails 第九子页(规则清单+启停+审计分页);
  输入被护栏拦截的问答走 REJECTED 分态文案(前端零新形态)。

## Capabilities

### New Capabilities

- `prompt-guardrails`: 输入/输出/工具意图三层护栏的行为契约、fail-closed
  语义、护栏审计、规则配置与管理、行级可见性。

### Modified Capabilities

- `chat`: 问答链路输入护栏挂接(拦截落 REJECTED 消息的既有形态复用)。
- `tool-calling`: 工具执行回调的意图审核挂接点(在参数 JSON Schema 校验
  之前)——MODIFIED 六层防御 requirement 补意图审核层语义。

## Non-goals

- 不做 LLM 语义级护栏判定(语义分类模型/embedding 相似度,v1 全规则
  确定性判定;LLM 判定的成本与误判面随规模化评估)。
- 不做内容安全的政治/暴恐等广义审核词库运营(v1 基础违规关键词集,
  词库运营属内容运营而非本变更)。
- 不改 018 质量向护栏(prompt 规则资产);不改 019 工具防御顺序语义
  (意图审核=挂接点插入,非重排)。
- 不做输出流的逐 token 实时过滤(SSE 过程帧不过护栏;终态 summary/答案
  过输出护栏——过程帧只含阶段名/SQL/工具摘要,SQL 落库前已过防火墙)。
- 白名单粒度=规则级(某规则对全员停用),不做用户/租户级豁免清单
  (随运营诉求再加)。

## Impact

- **代码**:apps/api 新域包 `kongming/guardrail/`(models 规则引擎纯函数/
  service/router/repository);chat service 输入输出挂接(组合根注入端口);
  toolcall service 意图审核挂接(L4 前);alembic 0009(sys_guardrail_audit
  + sys_guardrail_rule 配置表);种子权限点 system:guardrail:manage(排序 15)。
- **契约**:管理端点(规则清单/启停/审计分页)入 OpenAPI;问答/工具
  端点零契约变化(护栏拦截走既有 REJECTED/rejected 形态)。
- **依赖**:零新依赖(规则引擎=正则/关键词纯 Python)。
- **ADR 引用**:ADR-004(新表迁移)、ADR-005(契约管线);信任边界不变
  (护栏全在 api 控制面,agent 零感知)。
- **安全**:本变更即安全域——测试先行(每类规则正反对拍用例);护栏
  拦截不暴露规则细节(对外文案统一「内容未通过安全检查」)。
