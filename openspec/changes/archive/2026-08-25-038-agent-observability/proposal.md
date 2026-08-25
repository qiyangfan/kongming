# 038 agent-observability — LLM 用量可观测域

## Why

多模型路由(021)落了逐次调用用量采集(sys_llm_usage)与聚合端点,但运维视角仍是盲区:
采集只记 tokens 不记耗时与成败(失败调用零记录,失败率不可见)、聚合端点无时间趋势与
明细、前端零看板(021 挂账⑤「可视化随 observability 等价域」明确指向本域);一次
问答横跨 LLM 轮次/工具执行/SQL 执行三张审计表,trace_id 已贯穿却没有按 trace 聚合的
时间线视图,排障只能逐表翻查。上游 023-agent-observability 已验证该域价值(调试/
成本控制/质量优化的运维地基),本变更是 roadmap 远期域中唯一未落地的实质域。

## What Changes

- **采集补全(agent 面)**:LlmClient 逐次调用补 latency_ms 与 status
  (SUCCESS/ERROR);失败调用(超时/断连/协议错)也构造记录上报(usage 缺省 0),
  fire-and-forget 与 bot 请求内收集器两通道同步扩展。
- **表结构**:`sys_llm_usage` 加 `latency_ms INTEGER NULL` 与
  `status VARCHAR(16) NOT NULL DEFAULT 'SUCCESS' CHECK IN ('SUCCESS','ERROR')`
  (迁移 0025;存量行默认 SUCCESS 语义成立——021 起失败调用本就不落记录)。
- **聚合强化(api)**:`/system/llm-usage/summary` 响应扩 calls_error(失败次数)与
  latency_ms_avg/latency_ms_max;新增按天趋势端点(Asia/Shanghai 分桶逐天补零,
  014 overview 同构)与明细分页端点(筛选 model/task/status/trace_id)。
- **trace 时间线(api)**:新增 `GET /system/llm-usage/timeline?trace_id=`,
  组合 sys_llm_usage(调用量)、sys_tool_audit(工具执行,duration_ms/status)、
  sys_sql_audit(SQL 执行,verdict)三表按时间序拼装,形成一次请求的全链路事件流。
- **看板 UI(web)**:/admin 第十七子页「用量」(权限点 system:llm-usage:view):
  汇总卡(calls/tokens/失败率/平均延迟)+按天趋势图(echarts,token 色板)+
  模型×任务对比表+明细分页+「时间线」对话框(按 trace_id 查事件流)。
- 搭车:docs/roadmap.md 状态同步(第三梯队全落地、第二梯队 auth spec 已同步、
  observability 自 D 层转入落地;rag-refine 已由 037/014 覆盖的处置注记)。

## Capabilities

### New Capabilities

- `observability`:LLM 用量观测面板与 trace 时间线——聚合查询(趋势/对比/明细,
  含失败与延迟统计)、trace 三表时间线、权限与 fail-open 口径、看板交互。

### Modified Capabilities

- `multi-model-routing`:「用量采集与统计」requirement 扩字段——逐次调用补
  latency_ms 与 status,失败调用也采集(原条款仅 tokens 且 usage 缺失即不落记录);
  聚合端点行为随观测域强化(该 requirement 的采集面句保留,统计端点句指向
  observability 域补全)。

## Impact

- **表结构(ADR-004)**:迁移 0025 给 sys_llm_usage 加两列(ALTER 型,存量行
  DEFAULT 补位);无新表。
- **api**:llmusage 域包(repository 聚合/趋势/明细/时间线查询、router 新端点与
  response_model 扩展、/internal/llm-usage 接收新字段);audit/toolcall 域零改动
  (时间线在 router 组合根并行调既有 repository,组合根模式先例=chat 组合根)。
- **agent**:llm.py _report 记录构造扩字段+调用点包耗时与异常路径(四处调用点);
  零新依赖。
- **web**:新页 UsagePage+AdminLayout 注册;类型经 make contracts 生成(ADR-005)。
- **契约**:summary 响应扩字段+新端点 3 个(trend/page/timeline),make contracts
  重生成。
- **权限**:零新增(沿用 system:llm-usage:view,仅 SUPER_ADMIN 种子覆盖)。
- **Non-goals**:
  - 不做成本换算与落库(模型单价在 agent 注册表 env,api 侧不可达;计费口径归
    billing 域既有月度出账,展示侧合并随该域演进)。
  - 不做 prompt/response 摘要落库(数据体量+脱敏面;失败排障靠日志+时间线,
    上游 span 摘要形态不移植)。
  - 不做人工标注与 eval 数据集导出(014 反馈赞踩已有;导出反哺随 eval 域评估)。
  - 不做行级可见性切分(sys_llm_usage 无 user 维度;观测面按租户聚合+权限点
    管理,与上游 data_scope 行级隔离的差异有 spec 立约注记)。
  - 不做 OpenTelemetry/外接 APM 接入(技术栈锁定,自建表+页面即止)。
  - 不动 015/019 审计页既有筛选 UI(同族挂账随各域变更搭车,一个 change 一个
    能力域)。
