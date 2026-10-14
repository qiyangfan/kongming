# Tasks

## 1. 测试先行(先红)

- [x] 1.1 `tests/test_rag_fulltext_config.py` 对拍重钉:兜底谓词带 `length(tok) >= 2` 内容词过滤的期望 SQL;桩恒空=AND+兜底两条;轮次纪律桩(AND 命中=仅 1 条);缓存键断言含 `and-cw` 语义段
- [x] 1.2 摘除 098「查询客户」测试的 xfail(『客户』为内容词,兜底应转真);恢复「有多少部影片」兜底集成测试(表说明路+文档路);单字负证由对拍谓词字面量+实机探针承载(造「仅单字相交」集成数据过于脆弱,弃)

## 2. 实现

- [x] 2.1 `repository.py::recall_fulltext`:AND 零命中落内容词兜底轮(EXISTS + unnest 词条 + `length(tok) >= 2`),共享 SELECT 主体,排序/得分沿用同量纲 ts_rank
- [x] 2.2 `documents_repo.py::recall_children_fulltext` 同构改造
- [x] 2.3 `cache.py::_ctx_key` 纳入查询语义版本段 `and-cw`

## 3. 门禁与实证验收

- [x] 3.1 门禁全绿(1512 passed + 7 skipped;唯一失败=test_external_knowledge 已登记假败;xfail 转真 +4 恢复测试)
- [x] 3.2 实机探针:sakila 现态「有多少部影片」等问句经『影片』等内容词兜底命中(机制兑现);098「查询客户」集成测试首次真绿
- [x] 3.3 同指纹 evals(340dbcea)三轮定性:B' 净轮 49/32/45(与 A 轮失败名单逐题一致,表命中 -1);B'' 轮(含 evals 基建修复)**50/34/47 创新高**,失败 3 题全确定性 FILTER 归因;对齐口径(hex 两题按 A 轮恒失败计)宽松 49=49、严格 33>32、表命中 46=46——**验收线全过,采纳**。判定过程中的两个评估基建缺陷一并修复:①日配额耗尽污染(评估 api 缺 KM_CHAT_QUOTA_DAILY_CALLS=0 常备);②hex id 题租户推断 403(sakila.yaml 补显式 tenant)

## 4. 收尾

- [x] 4.1 spec delta 定稿(采纳形态+111 评估差异勘误);evals README 基线行与 111 行勘误;HANDOFF 分词节/基线段/评估配方(配额豁免)同步
- [x] 4.2 质量门禁复跑 + 变更归档(openspec archive)
