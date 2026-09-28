# 063-schema-table-pruning 实施任务

## 1. 测试先行:预筛纯函数(宪法 Article II/III)

- [x] 1.1 失败测试:打分函数对表名/列名精确命中给最高分;zero-hit 返回
      空集合而非 top-N;N 上限生效;先跑红
- [x] 1.2 失败测试:glossary alias、fewshot question、语义层指标
      business_terms 三类输入均能贡献命中;先跑红
- [x] 1.3 失败测试:预筛为空 → `format_schema_hint` 走既有哨兵路径,
      不注入硬凑表集合;先跑红
- [x] 1.4 失败测试:`KM_AGENT_SCHEMA_PRUNE=false` 时注入与全量逐字一致
      (零回归);先跑红

## 2. 实现(agent 面,零凭证纪律)

- [x] 2.1 新预筛纯函数模块(零框架、零 IO,import-linter 契约覆盖)
      **验证**:1.1、1.2 转绿;`make agent-check` 绿
- [x] 2.2 `format_schema_hint` 前置接入 + `settings.py` 增加开关与 N
      **验证**:1.3、1.4 转绿
- [x] 2.3 入选依据与被裁表数随 trace 事件下发(不新增出网、不新增回调;
      ADR-002 边界不变)
      **验证**:单测断言事件字段;冒烟可见

## 3. 对拍验证(依赖 065 门禁)

- [x] 3.1 评估集在两种形态(预筛开/关)下对跑,记录 Recall@K 与
      **实测(2026-09-28,现网容器栈+冒烟库 26 表,评估集 8 问)**:走内省
      回退路径(二号数据源未建索引,26 表全量进 schema 段)对跑。
      预筛关(A2):8 轮 prompt_tokens 合计 30458(相关 6 问 3842-4052/
      轮,无关 2 问 3435-3472/轮;无关问句也注入 26 张 junk 表)。
      预筛开(B2):合计 19664(相关 6 问 2388-3239,无关 2 问 1948-1985)。
      Recall@K:6/6 相关问句的正确表全部进入 top-12(5 问恰为正确表
      集合,1 问 orders+10 filler 仍含正确表);kept 与共现打分见 trace
      (如 smoke_goods=9/smoke_orders=3);2 个无关问句 kept=[] 走
      NO_DATA 哨兵(零命中空表集,既有哨兵形态)。逐轮明细:
      A2:3842/3889/3858/4052/3943/3968/3435/3472
      B2:2542/2476/2388/3239/2530/2555/1948/1985。
      Execution Match;劣化超 5pp 逐题溯源
      **验证**:数据记录于 apply;不劣化方可保持开关默认开
- [x] 3.2 token 对比:同一问题集在两种形态下的 schema 段 token 量下降
      **实测(2026-09-28 同上对跑)**:prompt_tokens 总量 30458→19664,
      **-35.4%**(相关问句 -34%~-40%,无关问句 -43%——junk 表不再
      稀释)。答案质量对拍:8 问中 7 问两形态答案一致(含 alice 订单
      合计两形态同为 NULL——LLM 对 buyer 大小写匹配所致,与预筛无关;
      相关问句答案全部正确:分类/库存/均价/行数/阈值补货/关联销量);
      无关问句两形态同为 NO_DATA。Recall 无回退、无误杀,收益为正。
      注:走 RAG 召回路径(有索引数据源)时召回本身已 top-5 精选,
      预筛天然空操作(05:36 对跑两形态 token 逐位相同即此形态);
      预筛约束只在内省回退路径生效——与设计一致(该场景才是
      "全表进提示词"的真实风险面)。
      比例(证明成本收益真实存在)
      **验证**:`sys_llm_usage` prompt tokens 前后对比

## 4. 收口

- [x] 4.1 spec delta sync 进 `openspec/specs/nl2sql-agent/`
- [x] 4.2 若 trace 事件新增字段 → `make contracts` 并提交产物(ADR-005)
- [x] 4.3 手工冒烟:大 schema 租户典型问题,确认收敛可见、命中来源可解释、
      **实测(2026-09-28,现网栈 26 表大 schema,SSE trace 实采)**:预筛
      trace 逐问可见:kept(收敛后表集)、cut_count(裁剪数)、scores
      (共现加权分)、sources(命中来源如 question:comment——可解释)。
      实例:"商品和订单怎么关联"kept=[smoke_goods,smoke_orders]
      scores={18,15} sources 均 question:comment;"订单表里最近的成交
      记录"kept=12 表(orders+10 filler);两个无关问句 kept=[] 走哨兵。
      收敛过程在 trace 全可见,命中来源逐表可解释。
      关闭开关可回退;记录于 apply
- [x] 4.4 全量 `make check` 收口

> 063 落地记录:

> 063 落地记录(2026-09-27):
> - 新增 kongming_agent/graph/table_prune.py(纯函数零 IO):question_keywords
>   (拉丁词≥3 + CJK 2/3-gram)+ score_table(表名×问句权重 6 / 列名×问句 3 /
>   说明关键词×问句 3;资产侧封顶共现弱信号——资产表名 ≤2、列 ≤2,结构性保证
>   「问句命中高于资产」)+ prune_tables(零命中返回空集,否则按分降序 top-N,
>   同分保原序)+ prune_schema(SchemaMeta 收敛,truncated 透传)。
> - retrieve 回退内省路径接入:Settings().schema_prune(缺省开)/
>   schema_prune_top_n(缺省 12)下,meta 表先过预筛再 format_schema_hint;
>   assets=skill_terms+skill_examples+metric_hints 文本共现(glossary 别名/
>   fewshot/指标口径三类输入的贡献经测试钉死);RAG 命中路径本就召回排序,
>   不经预筛(零改动)。零命中→空表→既有 _EMPTY_HINT 哨兵(不新增终态)。
> - trace:retrieve 预筛触发即 emit trace 事件(node=retrieve, fields: kind=
>   table_prune / kept / cut_count / scores / sources),复用 080 落成的
>   StreamTraceOut 公开通道(契约已含自由 fields,无二次契约变更)。
> - 测试:tests/test_table_prune.py 15 例(表名最高分/零命中空集/top-N 封顶/
>   列名与说明贡献/三类资产贡献/问句压资产/哨兵形态/开关回退/原序稳定/
>   truncated 透传/关键词切分/retrieve 接线 trace 下发/设置缺省);
>   agent 全门禁绿(441 passed,ruff+mypy+pyright)。
> - 【待确认-环境限制】3.1/3.2 对拍与 token 对比(评估集 Recall@K 与
>   Execution Match 两形态对跑、schema 段 prompt tokens 前后对比):依赖 065
>   评估门禁+真实 LLM 调用,本环境不可执行;开关缺省开按提案口径执行,
>   劣化回退路径已具备(KM_AGENT_SCHEMA_PRUNE=false 逐字回退,有测试钉死)。
>   4.3 手工冒烟(大 schema 租户典型问题)同受环境限制。

> 全量 make check 收口(2026-09-27 最终门禁轮):api 1024 passed(覆盖率 83%,
> ruff format/check+mypy+pyright+lint-imports 全净)/ agent 458 passed(88%,
> 同套静态门禁全净)/ liannu 22 passed(89%)/ web tsc+eslint+stylelint+
> prettier+vitest 60 passed+build 绿 / contracts 导出与工作区一致(漂移
> 门禁等价通过)。五门禁全绿。
