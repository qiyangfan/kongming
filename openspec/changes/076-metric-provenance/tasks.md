# 076-metric-provenance 实施任务

## 1. 测试先行:同源派生与透出契约(宪法 Article III)

- [ ] 1.1 失败测试:`format_metric_hints` 对结构化命中派生结果与现状逐字一致
      (同一命中集,改前改后字符串零差异——保证注入提示词零回归);先跑红
- [ ] 1.2 失败测试:召回响应同时携带 `metric_hints` 与 `metric_hits`,
      且 `metric_hints` 恰为 `metric_hits` 的派生(插入一条指标、问句命中,
      断言两侧数量与内容对应);先跑红
- [ ] 1.3 失败测试:无命中时 `metric_hits` 为空、`metric_hints` 为空,
      注入段与引入前逐字一致(零注入);先跑红
- [ ] 1.4 失败测试:终态(同步响应与流式 result)均携带口径依据,
      两路径逐字段一致;先跑红
- [ ] 1.5 失败测试:落库 `result_json` 含该键,历史回显值与当轮响应一致;
      老消息(无该键)回显为空且**不**反查指标表重算;先跑红
- [ ] 1.6 失败测试:`MetricDef` 携带标识与修订时间(仓储已 SELECT,
      断言 `enrich` 返回的命中含 `metric_id` 与 `updated_at`);先跑红

## 2. api 面实现(单一来源,零新增检索)

- [ ] 2.1 `semantic/models.py`:`MetricDef` 增 `metric_id` 与 `updated_at`;
      `enrich` 改返结构化命中 + 派生字符串(调用方取文字段处零改动)
      **验证**:1.1、1.6 转绿;`test_semantic*` 既有用例零回归
- [ ] 2.2 `rag/router.py` `RetrieveOut` 纯增 `metric_hits`;
      `retrieve_docs` 那条 try/except 的降级口径照旧(失败降级空列表)
      **验证**:1.2、1.3 转绿;`test_rag_recall` 既有断言零回归
- [ ] 2.3 `chat/models.py` 终态纯增该字段;落库与回显走同一序列化点
      **验证**:1.4、1.5 转绿;`test_chat_*` 既有用例零回归

## 3. agent 面实现(ADR-002 零凭证纪律不变)

- [ ] 3.1 `apiclient.py` 解析 `metric_hits`;`graph/state.py` 纯增态;
      `nodes.py` retrieve 后组包(空则跳过)
      **验证**:`test_graph_topology` 零回归;既有 stream 用例零回归
- [ ] 3.2 `main.py` `QueryOut` 纯增该字段
      **验证**:1.4 转绿

## 4. 契约与前端

- [ ] 4.1 `make contracts` 重新生成并提交产物(ADR-005)
      **验证**:`git diff contracts/openapi.json` 仅本次新增字段,无意外差异
- [ ] 4.2 `AssistantCard` 新增「口径依据」折叠面板(复用 `collapsible`,
      空不渲染;指标名跳 `/admin/semantic`);doc_answer 路径同样有则展示
      **验证**:亮暗两态 + 375/768/1280 三档走查;`pnpm typecheck && pnpm lint` 绿
- [ ] 4.3 宪法 Article 8 自检:面板取色全走 token,无字面色板、无 inline style
      **验证**:grep 零命中

## 5. 收口

- [ ] 5.1 spec delta sync 进 `openspec/specs/semantic-layer/`、
      `openspec/specs/chat/`、`openspec/specs/query-streaming/`
- [ ] 5.2 与并行提案的边界复核:确认未改 `doc_references`/`sources` 任何字段、
      未新增 SSE 事件类型(与 `068-doc-answer-citations` 无重叠);
      `result_json` 新增键与它们的键不冲突
      **验证**:`git diff contracts/openapi.json` 中两类字段各自独立
- [ ] 5.3 手工冒烟:配一条指标(如「复购率」)→ 问句命中 → 确认终态与面板
      展示该指标口径与修订时间 → 刷新后回显一致 → 改指标定义后新问答面板
      同步、老消息仍是当轮快照;无命中问题确认无面板;记录于 apply
- [ ] 5.4 全量 `make check` 收口
