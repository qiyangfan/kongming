# 021 multi-model-routing:多模型注册表与任务级路由

## Why

agent 面目前单一 LLM 配置(llm_model 一刀切):SQL 生成、摘要、标题改写、
ReAct 决策全走同一模型,无法按任务特性选优(代码生成用强模型、轻量摘要
用快模型)也无法核算成本。本变更引入多模型注册表(能力标签+计价)+
任务级确定性路由 + 用量采集落库,为后续 Bot/工作流的模型覆盖(025 等)
与计费(developer-portal-billing)打底。单模型部署零回归(现有配置
原样工作)。

## What Changes

- **多模型注册表**(agent 侧,纯配置):经环境变量注入
  (KM_AGENT_MODEL_REGISTRY_JSON:名称/base_url/api_key/能力标签/单价);
  api_key 只在 agent 进程内存(ADR-002 边界内,LLM key 本就是 agent
  唯一外部凭证);注册表为空时回退现有 llm_* 单模型配置,行为逐字不变。
- **任务级路由**:任务类型枚举(SQL_GEN/UNDERSTAND/SUMMARIZE/TITLER/
  DESCRIBE/REACT)经路由表(KM_AGENT_MODEL_ROUTES_JSON)映射模型;
  未配置的任务回退主模型;路由决策确定性(同配置同结果,可审计);
  LlmClient.chat/chat_with_tools 增 task 形参(缺省主模型,调用方零改动
  零回归)。
- **供应商出口白名单**:注册表模型的 base_url 必须在白名单
  (KM_AGENT_MODEL_ALLOWED_HOSTS)内,不在则启动拒配(fail-closed,
  防端点注入)。
- **用量采集**:每次 LLM 调用采集 prompt/completion/total tokens +
  模型名 + 任务类型 + trace_id,agent 经内部回调上报 api
  (POST /internal/llm-usage,fire-and-forget 不阻塞主链路),api 落
  sys_llm_usage(Alembic 0010);按租户+模型+任务维度可统计
  (管理端点 GET /system/llm-usage/summary 聚合查询)。
- **embedding 独立配置既有语义不变**(009:embedding 不随主模型路由,
  上游 028 教训);embedding 用量同通道上报。

## Capabilities

### New Capabilities

- `multi-model-routing`: 模型注册表/任务路由/出口白名单/用量采集与统计的
  行为契约;单模型零回归条款。

### Modified Capabilities

- `nl2sql-agent`: LLM 客户端的任务级路由挂接(既有调用方零改动语义)。

## Non-goals

- 不做用量看板 UI(管理端点给聚合数据;可视化随 observability 等价域)。
- 不做计费/配额扣减(developer-portal-billing 域);不做 LangFuse 等外部
  观测平台接入(023 被宪法工程纪律覆盖,本变更只落库)。
- 不做运行时动态路由(负载/延迟反馈调权)、不做模型 fallback 链(单模型
  故障转移随 stability 评估);不做 streaming 计费差异。
- 不改 embedding 独立配置语义(009 已立约);不改 prompt 资产。

## Impact

- **代码**:apps/agent——settings(注册表/路由表/白名单解析,fail-closed
  校验)、llm.py(注册表多客户端+task 路由+usage 采集)、apiclient.py
  (usage 上报回调)、nodes/react 节点传 task 类型;apps/api——新域包
  kongming/llmusage/(repository 落库+聚合查询+router 内部端点+管理端点),
  alembic 0010(sys_llm_usage)。
- **契约**:/internal/llm-usage + /system/llm-usage/summary 入 OpenAPI。
- **依赖**:零新依赖。
- **ADR 引用**:ADR-002(usage 上报=agent→api 回调同范式)、ADR-004
  (新表)、ADR-005(契约)。
- **安全**:api_key 仅 agent 内存(env 注入,不落库不落日志);白名单
  fail-closed;上报通道内部令牌。
