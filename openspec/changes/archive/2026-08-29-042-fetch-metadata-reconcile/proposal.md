# Fix: fetch-metadata 对账语义(登记=现实,残留自愈)

## Why

2026-08-29 事故(default 租户 + default 源问 sakila 问题报
`relation "public.staff" does not exist`)经两轮定性修正后结论:

- **直接根因不在知识层**:default 租户名下并无 sakila 知识(那 28 行
  `tenant_sakila.*` 属 sakila 租户,是 load.sh 刻意装载的租户路径合法数据);
  召回隔离与 few-shot 通道均干净。LLM 是**凭训练先验编造**了 sakila SQL
  ——`_EMPTY_HINT` 占位文案「请基于问题尽力生成」在无表结构时实为教唆
  编表,`SQL_GEN_SYSTEM` 亦无「仅限所给表、未覆盖须如实说明」纪律。
  该缺口独立立项 043(生成纪律),不在本变更范围。
- **但定性过程暴露的机制缺口独立成立**:知识登记(`_ingest`)只有
  「追加/覆盖」语义,没有「对账」语义——拓扑变更(源迁移/schema 收窄)
  后,扫描集之外的孤儿登记会永久留存,清理依赖人工记忆。同构事故
  (039 期 26 表知识键错配)已发生过一次;本次 default 源重跑
  fetch-metadata 实证 `removed=0`(sakila 租户的 28 行在其自身扫描集内,
  机制未误删合法行),反向证明对账口径精确。

040(closed-set)已实现「登记范围与执行可达范围同源」(`allowed_schemas_for`
唯一计算点)与四元幂等 upsert;本变更补齐最后一块:fetch-metadata 收口时
**登记结果 = 本次扫描到的现实**,孤儿登记由机制自愈。

## What Changes

- **rag 对账(核心)**:`_ingest`(fetch-metadata 专享)在 upsert 收口后、
  同事务内删除该 (tenant, datasource) 下**自动来源**(INTROSPECTION/
  AI_GENERATED)且不在本次扫描集的行;扫描集外的人工/反馈行
  (MANUAL_UPLOAD/MANUAL_EDIT/FEEDBACK_REFINED)保留并告警
  (不可逆资产交人工裁决)。空扫描集同样对账(keep=∅)。
- **预读键二元化(正确性顺手修)**:`list_source_and_alias` 预读 dict 键由
  table_name 一元改为 (schema_name, table_name) 二元——同名表跨 schema
  (tenant_default.goods 与 public.goods)不再互相遮蔽优先级判定与生效别名;
  消费方 `_ingest`/`upload_blocks` 同步。
- **契约**:FetchResult/FetchOut 增加 `removed`(本次对账删除行数);
  `make contracts` 同步。
- **机制实证代替手删**:对 default 源重跑 fetch-metadata,断言合法行
  零误删(removed=0);以事故同题复现固定 043 的立项证据。
- 测试先行:对账删自动孤儿/人工孤儿保留/removed 计数/二元键不遮蔽。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `knowledge`:fetch-metadata SHALL 具备对账语义——收口后该
  (tenant, datasource) 的自动来源登记集合 SHALL 等于本次扫描集;
  扫描集外人工/反馈条目 SHALL 保留。

## Non-goals

- **043 生成纪律**(事故直接根因的修复:表范围纪律+空结构如实说明+哨兵短路,
  agent 面 prompts/nodes,独立立项待拍板);
- `public.user_memory` 是否收编 RESERVED_TABLE_NAMES(既有上报项,
  HANDOFF 待裁决,与本次对账口径无关——对账只删扫描集外自动来源,
  user_memory 在扫描集内不受影响);
- default 源形态调整(平台主库兼业务数据源是产品默认形态);
- reindex 语义(全删重建天然对账,不动);
- rag ctx 缓存结构/TTL(D6 钩子已覆盖对账删除的失效路径)。
