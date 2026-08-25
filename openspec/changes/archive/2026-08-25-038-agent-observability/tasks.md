## 1. 表结构与采集(测试先行)

- [x] 1.1 迁移 0025:sys_llm_usage 加 latency_ms/status 两列(存量 DEFAULT
  'SUCCESS',CHECK 约束,downgrade 删两列);alembic upgrade 验证
- [x] 1.2 agent llm.py:_report 记录构造扩 latency_ms/status(去掉 usage None
  短路,按 status 驱动);chat/chat_with_tools/embed 四调用点包
  time.monotonic 计时+异常路径先记 ERROR 再 raise;收集器通道 record 纯增
  字段;agent 单测补失败采集/latency 在场断言
- [x] 1.3 api /internal/llm-usage 接收面:UsageRecordIn 加 latency_ms/status
  (缺省兼容旧上报),repository insert_batch 落新列;bot sink 透传两字段,
  落库测试断言写入

## 2. 聚合与时间线端点(测试先行)

- [x] 2.1 repository.summary 扩聚合:calls_error/latency_ms_avg/latency_ms_max
  (仅 SUCCESS 且非 NULL 参与延迟统计);router summary 响应扩字段
- [x] 2.2 repository.trend:generate_series 按天分桶逐天补零,窗口与分桶统一
  AT TIME ZONE 'Asia/Shanghai';router GET /system/llm-usage/trend(days 1..90
  默认 30);跨月边界测试
- [x] 2.3 repository 明细分页(created_at 倒序+model/task/status/trace_id
  筛选+tenant 反解 unknown);router GET /system/llm-usage/page;筛选分页测试
- [x] 2.4 timeline:SqlLlmUsageRepo/toolcall/audit 三 repo 各备按 trace 只读
  查询(缺则加 list_by_trace,零行为变更);router GET
  /system/llm-usage/timeline 组合三路按 (created_at,id) 升序输出统一事件
  (kind/name/status/tokens?/latency_ms?/ts);空流 200;三表拼装测试
- [x] 2.5 权限:四端点挂 system:llm-usage:view,无权限 403+权限拒绝审计
  测试(既有 require_perm 机制核对)

## 3. web 看板

- [x] 3.1 make contracts 重生成;web lib 侧 api 封装(类型全生成,禁手写)
- [x] 3.2 UsagePage:汇总卡组+按天趋势图(echarts,token 派生色板)+模型×
  任务对比表(calls_error 红色 badge)+明细分页(四筛选+trace_id)+行内
  「时间线」Dialog(事件流纵向时间线,kind 图标+耗时+状态)
- [x] 3.3 AdminLayout 注册第十七子页「用量」(perm=system:llm-usage:view),
  无权限直达 403 品牌页;React Query 键含筛选维度

## 4. 验证与收尾

- [x] 4.1 make check 全绿(api/agent ruff+mypy+lint-imports+pytest+web
  tsc+eslint)+make contracts 无漂移
- [x] 4.2 冒烟:真 LLM 问答后 usage 页全要素(汇总卡/趋势/明细含 SUCCESS 行
  与 latency)/构造 ERROR 记录失败率可见/timeline 三类事件时间序/无权限
  403;Playwright UI 走查+截图记录
- [x] 4.3 docs/roadmap.md 状态同步(第三梯队全落地/第二梯队 auth 已同步/
  observability D→落地/rag-refine 由 037+014 覆盖注记);HANDOFF 段更新
- [x] 4.4 归档流程:openspec validate→archive,sync observability 新主 spec
  +multi-model-routing delta 进主 spec
