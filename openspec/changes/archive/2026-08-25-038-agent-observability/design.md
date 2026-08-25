# 038 agent-observability 设计

## Context

sys_llm_usage(0010 建表,0023 扩 bot_id/seq)现有列:id/trace_id/tenant_id/
model/task/prompt_tokens/completion_tokens/total_tokens/bot_id/seq/created_at。
采集双通道(multi-model-routing spec 立约):bot 链路请求内收集器随响应同步入账
(BotService sink),其余链路 agent fire-and-forget 上报 /internal/llm-usage。
agent LlmClient._report 在 usage is None 时直接 return——失败调用零记录是本域
要消除的盲区起点。聚合端点 /system/llm-usage/summary 仅 GROUP BY 租户+模型+
任务合计。sys_tool_audit(status/duration_ms/trace_id)与 sys_sql_audit
(verdict/trace_id)两表时间线素材齐备。web admin 已有十六子页
(AdminLayout ADMIN_NAV 数组注册),014 FeedbackPage 已有"统计卡+按天趋势"
页面形态可复用。

## Goals / Non-Goals

**Goals:**
- 失败可见:失败调用留痕(零 token 记录),失败率可聚合。
- 一次请求全链路可回放:按 trace_id 三表事件流。
- 运营看板零学习成本:复用 admin 子页+014 趋势形态。

**Non-Goals:**
- 成本换算、span 级 prompt/response 摘要、人工标注/eval 导出、行级可见性、
  OpenTelemetry——见 proposal Non-goals。
- 不改 audit/toolcall 域既有端点与行为(时间线只读复用其表)。

## Decisions

### D1 失败采集在 LlmClient 调用点内闭合

chat/chat_with_tools/embed 四处调用点改为:进入时 `time.monotonic()` 起点,
正常路径 _report(usage=resp.usage, status=SUCCESS, latency_ms=毫秒);异常
路径先 _report(usage=None, status=ERROR, latency_ms=实际耗时)再 raise。
`_report` 去掉「usage is None 即 return」短路,改按 status 驱动(usage 取
getattr 默认 0,与现记法一致)。备选=在 apiclient 上报侧兜底——被否:那里
拿不到 latency 与 model/task 语境,且 embed 分批在内层。注意 035 收集器通道
同样受益(record 结构纯增字段,sink 侧透传),fire-and-forget 语义逐字保留
(上报调度失败仅日志)。

### D2 迁移 0025:两列 ALTER,存量默认 SUCCESS

`latency_ms INTEGER NULL`(NULL=未知,不参与延迟统计)与
`status VARCHAR(16) NOT NULL DEFAULT 'SUCCESS' CHECK (status IN
('SUCCESS','ERROR'))`。存量行补 SUCCESS 语义成立(021 起失败调用本就不落
记录)。downgrade 删两列。无新表、无数据回填。

### D3 聚合三端点形态(挂 /system/llm-usage)

- `summary` 扩响应字段:每分组加 `calls_error`(status='ERROR' 计数)与
  `latency_ms_avg`/`latency_ms_max`(**仅 SUCCESS 且 latency_ms 非空参与**,
  失败耗时语义归时间线,聚合指标保持成功口径干净;avg 取 NULL 当组内无样本)。
- `trend?days=N`(1..90,默认 30):generate_series 按日历日分桶逐天补零,
  分桶与窗口边界统一 `AT TIME ZONE 'Asia/Shanghai'`(034 S2 教训),返回
  [{date, calls, calls_error, total_tokens}]。
- `page`(page/size+model/task/status/trace_id 筛选):created_at 倒序分页,
  tenant 经 LEFT JOIN 反解 tenant_key(NULL→"unknown"),015 audit page 同构。
- `timeline?trace_id=`:router 组合根并行 await 三个 repository 查询
  (SqlLlmUsageRepo.list_by_trace / toolcall 域 repo 按查 / audit 域 repo 按
  trace——后两者若无现成方法则各加只读 list_by_trace,零行为变更),merge
  后按时间升序输出统一事件 {kind: LLM|TOOL|SQL, name, status, tokens?,
  latency_ms?, ts}。SQL 事件 status 映射 verdict 直传。空流 200。
  备选=service 层组合——被否:llmusage service 引 audit/toolcall repo 属跨域
  依赖,router 适配层组合是 chat 组合根先例的既定模式。

### D4 内部上报接收面纯增字段

`/internal/llm-usage` 的 UsageRecordIn 加 `latency_ms: int | None =
Field(default=None, ge=0)` 与 `status: Literal["SUCCESS","ERROR"] =
"SUCCESS"`;bot 收集器 sink 落库侧同步透传两字段。缺省值保证旧 agent
(滚动升级窗口)上报仍被接受,契约向后兼容。

### D5 权限与审计

四端点全挂既有 `system:llm-usage:view`(require_perm 403 落权限拒绝审计,
既有机制零改动);零新权限点、零种子变更。

### D6 web 第十七子页「用量」

UsagePage:顶部汇总卡组(calls/tokens/失败率/平均延迟,由 summary 聚合前端
合并)+按天趋势线图(echarts,色板从 CSS token 派生,011 铁律)+模型×任务
对比表(calls_error 红色 badge)+明细分页(筛选+trace_id 复制)+行内
「时间线」按钮开 Dialog 展示事件流(纵向时间线,kind 图标+耗时/状态)。
AdminLayout ADMIN_NAV 注册({to:"/admin/usage",label:"用量",perm:
"system:llm-usage:view"});类型经 make contracts 生成消费。备选=时间线嵌入
明细行展开——被否:事件数不定,Dialog 更稳。

## Risks / Trade-offs

- [llm.py 四调用点改造引入回归] → 既有 agent 测试面(105+)全量回归;
  _report 改动点集中单函数+调用点仅增参数。
- [趋势分桶时区错位(034 同族)] → SQL 内统一 AT TIME ZONE 'Asia/Shanghai',
  边界测试锁定(跨月窗口)。
- [timeline 三表时间戳语义混排(事务内 created_at 精度)] → 排序键取
  (created_at, id) 稳定序;时间线定位排障足够,不做因果推断。
- [存量 bot 渠道 sink 漏透传新字段] → sink 落库测试断言 latency_ms/status
  写入;缺省 DEFAULT 兜底不炸。
- [大租户明细页深翻页性能] → 索引既有(created_at DESC/model/time);管理页
  量级可接受,游标分页不引入(v1)。

## Migration Plan

1. 迁移 0025(ALTER 加两列,空表瞬时语义,存量表亚秒)。
2. agent/api/web 同批实施(无中间不兼容态:D4 缺省值兼容旧上报)。
3. 回滚:downgrade 0025 删两列;agent 上报新字段会被旧 api 422——回滚须
   同批退 agent(单仓单批部署,窗口无实害)。

## Open Questions

- 明细页是否需要租户筛选下拉(数据 tenant 聚合已可见;v1 不加,观察用)。
- 趋势图双轴(tokens 柱+calls 线)还是单轴切换——实施时按 014 趋势形态
  就近选择,无契约影响。
