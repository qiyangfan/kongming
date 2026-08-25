# 020 prompt-guardrails-security 设计

## Context

019 已落地:ReAct 内核 + /internal/tool/execute 六层防御 + sys_tool_audit;
chat 链路 _prepare_query/_finalize_reply 前后段共用;017 REJECTED 分态文案
形态就位。本变更在其上插入 prompt 层三层护栏。约束:宪法分层(新域包
guardrail)、ADR-004 迁移、测试先行(安全规则类)、零新依赖。

## Goals / Non-Goals

Goals:三层护栏确定性规则引擎 + fail-closed + 全量审计 + 超管可配置。
Non-goals 见 proposal(LLM 语义判定/词库运营/流式过程帧过滤不做)。

## Decisions

### D1: 护栏全在 api 控制面,agent 零感知

护栏判定全为确定性文本规则(正则/关键词/特征片段比对),无 LLM 调用,
放 api 侧:审计落库零跨进程;agent 完全无感(信任边界不动,ADR-002
零新风险)。备选「agent 侧 understand 前挂接」否决:审计落库须回调 api,
徒增往返且护栏配置要双进程同步。

### D2: 三个挂接点

- **输入**:chat service query/start_stream 在 _prepare_query 之后、agent
  调用之前。question 与 history 各条都过。拦截→assistant 落 REJECTED
  消息(persist 时,USER 消息已在 prepare 落)+ ChatOutcome(success=
  false, verdict="BLOCKED", error=安全检查文案)——复用既有三态映射与
  REJECTED 分态,前端零新形态。脱敏→question 替换为脱敏值继续。
  USER 消息落库保留原始问句(用户自己输入的内容,回显给他看;进 LLM
  的才是脱敏值)——口径:审计/落库记原文,LLM prompt 用脱敏值。
- **输出**:_finalize_reply 内对 reply.summary 过输出护栏(拦截→替换
  安全文案;脱敏→掩码),insights[].text 同过。columns/rows 不过(业务
  数据正当可见,spec 已立约)。
- **意图**:toolcall service execute_tool 的 L4 参数校验之前:查本
  trace_id 的写工具已有调用数(sys_tool_audit 含 PENDING_CONFIRM/
  EXECUTED/REJECTED 全部——计数=尝试次数),≥阈值即拒
  (GUARDRAIL_BLOCKED);双审计落库(spec 立约)。

### D3: 规则注册表 + 配置覆盖合并

`guardrail/rules.py` 代码注册表:GuardrailRule(name, guardrail_type,
action 缺省, enabled 缺省, pattern/description/threshold)。配置表
sys_guardrail_rule(rule_name PK, enabled, action 覆盖, threshold,
updated_at, updated_by)只存覆盖项;判定器每请求合并(注册表全量 +
库覆盖;表行数小,全表读免缓存失效面)。库读失败=fail-closed(全部
按缺省 enabled 且拦截类动作从严)。

### D4: v1 规则集(代码注册表内置)

输入(input):
- inj_ignore_instructions:/忽略.{0,6}(指令|指示|要求)|ignore\s+(all\s+)?(previous\s+)?instructions/i → blocked
- inj_jailbreak:扮演.?DAN|jailbreak|越狱模式|进入开发者模式 → blocked
- inj_prompt_leak:系统提示词|你的(提示词|指令)|输出.{0,4}(指令|提示词)|system\s*prompt → blocked
- pii_phone:1[3-9]\d{9} → masked(138****5678)
- pii_idcard:\d{17}[\dXx] → masked(前 6 后 4)
- pii_bankcard:\d{16,19}(带边界负向断言防误伤) → masked(****)
- pii_email:[\w.+-]+@[\w-]+\.[\w.]+ → masked(***@domain 保留域名)
- cred_apikey:(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}) → masked
输出(output):cred_* 同上 + out_sysprompt_leak=系统提示词特征片段集
(SQL_GEN_SYSTEM/REACT_SYSTEM/SUMMARIZE_SYSTEM 的特征句,长度≥12
中文连续片段,如「统计/明细边界」「排序键可能并列」)→ blocked 替换。
意图(tool_intent):write_burst(写工具 trace 内计数阈值,缺省 1,可配)。

### D5: 迁移(ADR-004)

alembic 0009:sys_guardrail_audit(id uuidv7, trace_id VARCHAR(64),
user_id UUID, tenant_id UUID, guardrail_type VARCHAR(16) CHECK
(input/output/tool_intent), rule_name VARCHAR(64), action VARCHAR(16)
CHECK(blocked/masked), content_digest TEXT[脱敏摘要,截断 500],
created_at;索引 (tenant_id, created_at DESC)、(guardrail_type, created_at
DESC)) + sys_guardrail_rule(rule_name VARCHAR(64) PK, enabled BOOLEAN
NOT NULL, action VARCHAR(16) NULL, threshold INT NULL, updated_at
timestamptz, updated_by UUID)。

### D6: 权限点与管理端点

system:guardrail:manage(排序 15,SUPER_ADMIN 覆盖;不推荐 DEVELOPER)。
端点:GET /system/guardrails/rules(合并视图清单)+ PUT
/system/guardrails/rules/{name}(enabled/action/threshold 三态更新)+
GET /system/guardrails/audit/page(类型/规则/动作/时间筛选,行级可见性
非 ALL 强制本人)。web /admin/guardrails 第九子页。

### D7: fail-closed 收敛

判定器入口 try/except Exception 全收敛:输入侧异常→按 blocked 处理
(REJECTED);输出侧异常→替换安全文案;意图审核异常→rejected。
全部落审计(content_digest 标 fail-closed 原因前缀)。护栏自身 bug
永不成绕过面。

### D8: chat 组合根端口注入

chat 契约禁 service import guardrail 仓储:GuardrailPort 端口进
chat.ports(evaluate_input(question+history) → verdict;evaluate_output
(text) → text 替换后),组合根绑 guardrail service。toolcall service 加
IntentReviewer 端口(复用 ToolRepo 查询,无需新仓储)。

## Risks / Trade-offs

- [正则误杀正常问句(如「列出系统提示词表」撞 inj_prompt_leak)] → 规则
  措辞窄化(动词+目标共现);超管可禁用单条规则止血;误杀案例进评估集
  观察。
- [特征片段泄漏检测误报(答案正常引用规则词)] → 特征片段取足够长
  (≥12 连续字符);误报则替换文案影响仅摘要,结果集不受影响。
- [每请求读配置表] → 行数极小(≤20),单 SELECT 开销可忽略;免缓存
  失效面。
- [输入护栏给恶意用户oracle(拦截即知规则存在)] → 对外文案统一
  「内容未通过安全检查」不点名规则(规则名只在审计与管理面可见)。

## Migration Plan

1. 0009 两表(空表瞬时);2. 种子权限点(只增);3. 配置表零行=全注册表
缺省生效(灰度语义:上线即启用,禁用走管理面);4. 回滚=禁用全部规则
(配置表)即回老行为,表留库无害。

## Open Questions

- O1:history 各条过输入护栏的口径(全量逐条 vs 仅当前问句)——v1
  全量逐条(历史可能含早先注入);性能实测无忧。
- O2:输出护栏对 data_preview 型摘要(stats 数字含手机号形态)误判面
  ——观察记录,误判案例进评估。
