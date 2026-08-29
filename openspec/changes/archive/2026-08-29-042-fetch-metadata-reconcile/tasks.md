# Tasks: 042-fetch-metadata-reconcile

## 1. 测试先行(失败测试)

- [x] 1.1 `_seed_one` 增 `schema` 参数(默认 tenant_default,向后兼容)
- [x] 1.2 用例:扫描集外自动孤儿被删+removed 计数+人工孤儿保留(全栈)
- [x] 1.3 用例:跨 schema 同名表优先级判定不遮蔽(全栈)
- [x] 1.4 用例:FetchOut 契约含 removed 字段

## 2. 实现

- [x] 2.1 ports.RagRepo 增 `delete_stale_entries`(窄端口)
- [x] 2.2 repository:实现对账 DELETE(unnest 平行数组绑定参数,AUTOMATED_SOURCE_TYPES 常量口径)+ 人工孤儿计数;`list_source_and_alias` 键二元化
- [x] 2.3 service:`_ingest` 收口对账(keep=全量扫描集)+审计 detail(removed/manual_orphans)+人工孤儿告警;空集分支走对账;`upload_blocks`/`_ingest` 预读消费点二元键;FetchResult.removed
- [x] 2.4 router:FetchOut.removed

## 3. 门禁与契约

- [x] 3.1 `make contracts`(openapi.json + api-types.gen.ts 同步)
- [x] 3.2 web RagPage fetch 文案补「清理 N 条失效条目」(removed>0)
- [x] 3.3 `make check` 全绿(api 860+agent 180 ruff+mypy+pytest、web tsc+eslint;
  首轮 api 唯一失败经错峰重跑判定为共享测试库竞争假失败)

## 4. 机制实证与验证(机制自身执行)

- [x] 4.1 default 源重跑 fetch-metadata:removed=0(sakila 租户 28 行在其自身
  扫描集内=load.sh 租户路径合法数据,零误删;初判「default 源残留待删」系
  租户维度误读,proposal/design 已修正)
- [x] 4.2 事故同题复现:default 租户 + default 源仍编 public.staff——
  真根因=知识未覆盖时 LLM 凭先验编造(_EMPTY_HINT「尽力生成」教唆 +
  SQL_GEN_SYSTEM 无表范围纪律),非知识层问题 → 043 立项证据已固定
- [x] 4.3 对账/遮蔽用例全绿:自动孤儿删除+人工孤儿保留+removed 契约+
  跨 schema 同名表不互遮(test_rag_admin 42 passed)
