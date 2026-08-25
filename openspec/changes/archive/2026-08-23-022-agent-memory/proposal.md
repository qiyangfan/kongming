# 022 agent-memory:用户长期记忆

## Why

当前 Agent 每轮问答只有 5 轮会话内上下文,跨会话「越用越懂你」缺失:
用户偏好(「叫我小帆」「默认看 sakila 域」)与业务事实无法沉淀。本变更
落地用户长期记忆:显式/隐式双路径形成、向量召回注入问答链路、
置信度与遗忘、用户自管理(数据主权),user+tenant 双维 fail-closed 隔离。

## What Changes

- **user_memory 表**(Alembic 0011):content/category/confidence/source
  (EXPLICIT/IMPLICIT)/embedding(pgvector 1536 复用)/last_hit_at/dormant
  标志;user_id+tenant_id 双维。
- **召回注入**:问答前按当前问句向量召回 top-3 非 dormant 记忆(强制
  user_id+tenant_id 双过滤,跨用户/跨租户永不可见),拼「关于这位用户的
  已知信息」段经 /query 请求体纯增字段传给 agent;agent 在 generate 的
  user 提示拼接该段(系统模板资产零改动,009 历史段同款形态);无记忆
  时字段缺省,链路行为逐字不变(零回归)。
- **形成双路径**:显式(「记住…/请记住…」意图词,规则提取内容,
  confidence=1.0);隐式(问答成功后后台任务经 agent LLM 提取用户偏好/
  事实候选,confidence=0.5 opt-in——用户自管理页可确认/删除)。
  两路径形成前 SHALL 过 PII/凭证检测(复用 020 MASKERS 规则集,命中
  拒绝落库)。
- **遗忘与置信度**:confidence<0.3 或 >90 天未命中标 dormant(召回排除);
  召回命中刷新 last_hit_at。
- **自管理端点**(登录即可,数据主权=本人):GET /memory(列表,含
  dormant)+ PATCH /memory/{id}(编辑内容/确认隐式记忆)+ DELETE
  /memory/{id} + GET /memory/export(JSON 导出);越权操作他人记忆
  fail-closed 404 同形。
- **审计**:记忆形成/编辑/删除落 sys_oper_log(action=memory.*)。

## Capabilities

### New Capabilities

- `agent-memory`: 记忆存储与召回、双路径形成、PII 拒绝、遗忘与置信度、
  自管理端点的行为契约。

### Modified Capabilities

- `nl2sql-agent`: generate 用户提示拼接「已知信息」段的契约(系统模板
  零改动,无记忆零回归)。
- `chat`: 问答链路记忆召回挂接(响应契约零变化,纯链路内增强)。

## Non-goals

- 不做隐式记忆的 LLM 确认交互(「要记住吗?」反问);隐式记忆=直接落
  库 confidence=0.5,用户在自管理页确认/删除(opt-in 语义由自管理承载)。
- 不做团队记忆(team-workspace 域双路召回)、不做记忆共享/推荐。
- 不做 confidence 自动演化算法(命中加分/时间衰减 v1 固定值;演化随
  personalization 域评估)。
- 不改 020 护栏规则集(复用其 MASKERS 做形成前检测,不新增规则)。

## Impact

- **代码**:api 新域包 kongming/memory/(models/ports/service/repository/
  router);chat service 召回挂接(端口注入);agent main.py QueryIn 纯增
  memory_hint + nodes.py generate 拼接段(单行追加,系统模板不动);
  agent /internal/extract-memory 新端点(LLM 提取);alembic 0011。
- **契约**:/memory 四端点 + QueryIn 纯增字段 + /internal/extract-memory
  入 OpenAPI。
- **依赖**:零新依赖(pgvector/embedding 通道既有)。
- **ADR 引用**:ADR-002(embedding/提取经 agent 回调)、ADR-004(新表)、
  ADR-005(契约)。
- **安全**:双维 fail-closed 过滤测试先行;PII 拒绝落库复用 020 规则。
