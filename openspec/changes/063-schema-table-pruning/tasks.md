# 063-schema-table-pruning 实施任务

## 1. 测试先行:预筛纯函数(宪法 Article II/III)

- [ ] 1.1 失败测试:打分函数对表名/列名精确命中给最高分;zero-hit 返回
      空集合而非 top-N;N 上限生效;先跑红
- [ ] 1.2 失败测试:glossary alias、fewshot question、语义层指标
      business_terms 三类输入均能贡献命中;先跑红
- [ ] 1.3 失败测试:预筛为空 → `format_schema_hint` 走既有哨兵路径,
      不注入硬凑表集合;先跑红
- [ ] 1.4 失败测试:`KM_AGENT_SCHEMA_PRUNE=false` 时注入与全量逐字一致
      (零回归);先跑红

## 2. 实现(agent 面,零凭证纪律)

- [ ] 2.1 新预筛纯函数模块(零框架、零 IO,import-linter 契约覆盖)
      **验证**:1.1、1.2 转绿;`make agent-check` 绿
- [ ] 2.2 `format_schema_hint` 前置接入 + `settings.py` 增加开关与 N
      **验证**:1.3、1.4 转绿
- [ ] 2.3 入选依据与被裁表数随 trace 事件下发(不新增出网、不新增回调;
      ADR-002 边界不变)
      **验证**:单测断言事件字段;冒烟可见

## 3. 对拍验证(依赖 065 门禁)

- [ ] 3.1 评估集在两种形态(预筛开/关)下对跑,记录 Recall@K 与
      Execution Match;劣化超 5pp 逐题溯源
      **验证**:数据记录于 apply;不劣化方可保持开关默认开
- [ ] 3.2 token 对比:同一问题集在两种形态下的 schema 段 token 量下降
      比例(证明成本收益真实存在)
      **验证**:`sys_llm_usage` prompt tokens 前后对比

## 4. 收口

- [ ] 4.1 spec delta sync 进 `openspec/specs/nl2sql-agent/`
- [ ] 4.2 若 trace 事件新增字段 → `make contracts` 并提交产物(ADR-005)
- [ ] 4.3 手工冒烟:大 schema 租户典型问题,确认收敛可见、命中来源可解释、
      关闭开关可回退;记录于 apply
- [ ] 4.4 全量 `make check` 收口

> 063 落地记录:
