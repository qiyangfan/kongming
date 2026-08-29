# Design: fetch-metadata 对账语义

## 决策

### D1 对账口径:删自动、保人工、告警可见

- 删除集合:该 (tenant, datasource) 下 `source_type ∈ AUTOMATED_SOURCE_TYPES`
  (INTROSPECTION/AI_GENERATED)且 (schema_name, table_name) 不在本次扫描集。
  复用 models 既有 `AUTOMATED_SOURCE_TYPES` 常量,禁另起口径。
- 保留集合:扫描集外的人工/反馈行(MANUAL_UPLOAD/MANUAL_EDIT/
  FEEDBACK_REFINED)不删,service 层 log warning 报数——不可逆资产误删
  不可恢复,交人工裁决(管理面知识列表可见,可手工删除)。
- keep 集合 = **全量扫描集**(含因优先级被 skip 的表)——skip 的表现实中
  存在,登记不该被对账删除。

### D2 对账时机:同事务收口,复用既有失效钩子

DELETE 与 upsert 循环同会话同事务(`_ingest` 单 commit 收口,007 惯例),
审计 detail 记 `removed`/`manual_orphans`;commit 后既有
`_cache.evict(tenant, ds)` 钩子精确失效检索缓存——对账删除的缓存路径零新增。
空扫描集分支(现状 early return)改为同样走对账(keep=∅):扫描集为空
= 业务库该范围已无表,登记应清零;内省连接失败走异常路径不触发对账
(不会因连不上误删)。

### D3 端口形态:一次往返删+计数

`RagRepo.delete_stale_entries(tenant_id, datasource_key, keep) ->
tuple[int, int]`,实现为两条语句(同事务):
1. `SELECT count(*)` 扫描集外人工行 → manual_orphans;
2. `DELETE … AND NOT EXISTS (SELECT 1 FROM unnest(:schemas[], :tables[]) k(s,n)
   WHERE …) RETURNING` → removed。
keep 传平行数组绑定参数,禁字符串拼接;keep=∅ 时 NOT EXISTS 恒真=全删自动行。

### D4 预读键二元化(顺手正确性修)

`list_source_and_alias` 返回键改为 `(schema_name, table_name)` 二元——
040 收窄后 default 源合法范围含两个 schema(tenant_<key> + public),
同名表跨 schema 时一元键互相遮蔽:优先级判定拿错行(该 skip 的被覆盖/
该覆盖的被 skip)、生效别名串用。消费方 `_ingest`(优先级+生效别名)与
`upload_blocks`(生效别名预读——上传指定单一 schema,只该读本 schema
同名表)同步二元化。

### D5 契约与前端

- `FetchResult`/`FetchOut` 增 `removed: int`;HTTP 形态向后兼容(纯新增)。
- web RagPage 的 fetch 结果文案补「清理 N 条失效条目」(removed>0 时)。

### D6 机制实证代替手删

实施后对 default 源调一次 `POST /system/rag/fetch-metadata`:dev 库 default
租户名下无孤儿(28 行 tenant_sakila.* 属 sakila 租户、在其自身扫描集内,
是 load.sh 租户路径的合法数据——初判「default 源残留待删」系租户维度
误读,已修正),断言 removed=0、扫描集内行完好、合法行零误删;同题复现
(default 租户仍编 public.staff)固定 043 立项证据。治理动作与验收为
同一步,不手写任何 DELETE。

## 落点

| 文件 | 改动 |
|---|---|
| rag/ports.py | RagRepo 增 `delete_stale_entries` |
| rag/repository.py | 实现 D3 两条语句;`list_source_and_alias` 键二元化 |
| rag/service.py | `_ingest` 对账调用+审计+FetchResult.removed;空集分支;`upload_blocks` 预读消费点 |
| rag/router.py | FetchOut.removed |
| tests/test_rag_admin.py | `_seed_one` 增 schema 参;对账/遮蔽用例 |
| apps/web/src/pages/admin/RagPage.tsx | fetch 文案补清理计数 |
| contracts/openapi.json + api-types.gen.ts | `make contracts` 产物 |
