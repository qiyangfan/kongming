# 021 multi-model-routing 设计

## Context

agent 面 LlmClient(llm.py)单客户端:chat(chat_with_tools 019)+embed,
构造于 Settings(llm_base_url/llm_model/llm_api_key);embedding 独立客户端
(009)。全部调用点:8 节点图(understand/generate/summarize)+ ReAct
react_llm + titler + describer。约束:ADR-002(LLM key=agent 唯一外部
凭证)、prompt 资产零改动、016 评估基线不降。

## Goals / Non-Goals

Goals:注册表+任务路由+白名单+用量落库与聚合;单模型零回归。
Non-goals 见 proposal(看板 UI/计费/动态路由/fallback 链不做)。

## Decisions

### D1: 注册表与路由表 = env JSON 注入,启动解析 fail-closed

`KM_AGENT_MODEL_REGISTRY_JSON`: `[{"name","base_url","api_key","model",
"capabilities":[],"price_input"/"price_output"(可选,每百万 tokens 美分)}]`;
`KM_AGENT_MODEL_ROUTES_JSON`: `{"SQL_GEN":"deepseek-coder",...}`;
`KM_AGENT_MODEL_ALLOWED_HOSTS`: 逗号分隔主机名(缺省=主模型 base_url 主机
+dashscope 主机)。Settings 启动解析:畸形条目告警剔除;重名拒;白名单
外拒配;全拒且主模型缺 key 维持既有 warnings 语义。备选「管理面 CRUD
入库」否决:api_key 入库违反「凭证只在 agent」(上游 spec 明确 api_key
不入库);env JSON 可读性差由文档示例补。

### D2: LlmClient 多客户端池 + task 形参单点路由

LlmClient 构造时按注册表建 {name: AsyncOpenAI} 池(主模型恒在,
name="default");chat/chat_with_tools 加 `task: str | None = None`:
路由=ROUTES.get(task)→registry[name]→(client, model);任一环节未命中
回退主模型。usage 采集:resp.usage(prompt_tokens/completion_tokens/
total_tokens)+ model 实际用名 + task + trace_id。trace_id 传入:chat 加
可选 trace_id 形参(节点从 state 取;titler/describer 无 trace 传 None)。

### D3: 用量上报 = 独立回调,fire-and-forget

agent apiclient 加 report_usage(records: list)(POST /internal/llm-usage);
LlmClient 每次调用后 append 本地缓冲(进程内 list,cap 500 防内存),
后台 flush?——简化:每次调用后即试上报,失败仅日志(不阻塞主链路,
不重试不积压;丢弃可接受,用量是统计非计费依据——计费域落地时再评
可靠投递)。api 侧 llmusage 域包:router(/internal/llm-usage 内部端点
+ /system/llm-usage/summary 管理端点)+ repository(批量插入+聚合查询)。
内部端点传输层:records 上限 64 条/请求,字段宽度校验。

### D4: 迁移(ADR-004)

alembic 0010:sys_llm_usage(id uuidv7, trace_id VARCHAR(64) NULL,
tenant_id UUID NULL(上报可带,agent 不知道租户→由 api 端点上下文填:
agent 上报不携带租户——v1 tenant_id 记 NULL/后续 chat 链路可关联 trace
反查;**修正:agent 在各问答场景知道 tenant_key**——上报带 tenant_key,
api 解析落 tenant_id,解析失败 NULL),model VARCHAR(64), task VARCHAR(24),
prompt_tokens INT, completion_tokens INT, total_tokens INT, created_at;
索引 (created_at)、(model, created_at))。聚合查询按 tenant_key 反解
tenant 维度(查询时 JOIN sys_tenant;租户删除后 NULL 行保留)。

### D5: 节点任务类型接线

nodes.py:understand→UNDERSTAND、generate→SQL_GEN、summarize→SUMMARIZE;
react/nodes.py react_llm→REACT;titler→TITLER;describer→DESCRIBE;
embed 不参与(009 立约)。节点改动=chat 调用加 task= 与 trace_id= 两
参数,prompt/逻辑零改动(018 资产纪律兼容——只动调用点不动模板)。

### D6: 权限点与聚合端点

system:llm-usage:view(排序 16,SUPER_ADMIN 覆盖);GET
/system/llm-usage/summary?start&end → [{tenant_key, model, task, calls,
prompt_tokens, completion_tokens, total_tokens}](GROUP BY 聚合,
tenant_id LEFT JOIN tenant_key,NULL 归 unknown)。

## Risks / Trade-offs

- [env JSON 配置错误导致生产故障] → 启动解析告警剔除+回退主模型;
  白名单缺省含主供应商;settings 单测覆盖畸形形态。
- [usage 上报叠加每 LLM 调用一次 HTTP] → 主链路不 await 上报结果
  (create_task fire-and-forget);失败仅日志丢弃(非计费依据,v1 取舍);
  ReAct 循环多调用=多次上报,量小。
- [路由错误模型能力不足(弱模型生成 SQL 质量降)] → 路由是显式配置,
  默认全走主模型;016 评估基线复验红线;模型选型属运营决策。
- [tenant_key 反解失败(租户已删)记 unknown] → 聚合端点 LEFT JOIN,
  语义明确。

## Migration Plan

1. 0010 建表(零存量);2. 种子权限点;3. api/agent 滚动重启任意序
(agent 上报端点不存在时上报 404 仅日志,主链路无碍;api 先起更稳);
4. 回滚=配置不动即单模型零回归;表留库无害。

## R1 评审修复记录

M1(路由静默回退+启动校验缺位):fail_closed_checks 启动解析注册表/路由表
并对「路由目标∉注册表」逐条告警;_resolve 回退分支按 task+name 去重告警。
L2(usage 字段端到端无钉):agent 测试断言上报记录七字段全集。L4(import-
linter 第 11 契约):llmusage 无 models/service/ports 层(零业务规则),
契约无护的对象——tasks 3.2 标注不适用并记此(领域规则出现时补层补契约)。
L5(TITLER/DESCRIBE/EMBED 租户恒 unknown):取评审备选——明文取舍:旁路
调用(非问答主链路)用量归 unknown,spec「反解失败 NULL」语义兼容;
全链路签名扩散(6+ 文件)与收益不成比,随计费域重估。L6:ModelEntry
.api_key field(repr=False)。L7:计价字段 float 校验畸形剔除。
**挂账**:L3(注册表池每请求全量重建+不 aclose;惰性建池随规模);L8
(0010 依赖 0009/0008 未入库——三变更须同批按序提交,HANDOFF 登记)。
**观察**(行为等价/设计已接受):主模型不在 _pool(注释误导,行为等价);
fire-and-forget 关停窗口丢在飞任务(D3 明文);/internal/llm-usage
tenant_key 无绑定校验(内部令牌收口,无新增越权面);summary 朴素
datetime 时区歧义(管理面,影响小)。

## Open Questions

- O1:usage 可靠投递(本地批量缓冲+重试)——计费域落地时按计费精度
  要求重估(v1 统计口径丢弃可接受)。
- O2:按租户限额/配额(usage 反查限速)——developer-portal-billing 域。
