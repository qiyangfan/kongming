# Tasks

## 1. 测试先行(先红)

- [x] 1.1 `tests/test_rag_fulltext_config.py` 重钉对拍桩:recall_fulltext 桩恒空集场景期望 AND+OR 两条 SQL(零命中才落兜底的轮次纪律);缓存键布局断言补查询语义段
- [x] 1.2 新增 zhparser 集成测试(本机已装,真跑):「有多少部影片」形态问句经兜底轮命中「影片信息表」形态表说明;AND 有命中时不发第二条 SQL(优先级反证);文档路同构兜底集成测试

## 2. 实现

- [x] 2.1 `repository.py::recall_fulltext`:共享 SELECT 主体,AND 谓词零命中落 OR 兜底谓词(EXISTS + unnest 词条,plainto_tsquery 单词条安全形态),排序/得分沿用同量纲 ts_rank
- [x] 2.2 `documents_repo.py::recall_children_fulltext` 同构改造(同根因一次到位)
- [x] 2.3 `cache.py::_ctx_key` 纳入查询语义版本段(常量段,配置段后;evict SCAN 模式不变)

## 3. 门禁与实证验收

- [x] 3.1 门禁全绿(尝试形态:1512 passed,唯一失败=test_external_knowledge 本机代理已登记假败,与本变更无关)
- [x] 3.2 实机探针:zhparser 档 sakila/default 现态下「有多少部影片」等四问句经兜底轮产生全文路 hits(此前 no_hits);098 既有集成测试「查询客户」首次真绿
- [x] 3.3 同指纹 A/B evals(340dbcea):B 轮(OR 兜底)=宽松 45/53、严格 28/53、表命中 42/53,对比 A 轮(49/32/46)**三指标各 -4/53 系统性劣化**——验收线②(宽松/表命中不降)判负,单字虚词词条噪声命中挤占 RRF 融合位次所致;按提案即定验收线**回退**

## 4. 回退与收尾

- [x] 4.1 查询构造/缓存键/测试三处同步回退(对拍恢复 098 单 SQL 形态,兜底测试移除;098 既有「查询客户」集成测试转 xfail(strict) 标记 AND 虚词断裂 known-issue);门禁复跑零损(73 rag 域 passed+1 xfailed)
- [x] 4.2 实证否决契约化:spec delta 落「查询语义保持全词条 AND;任一词条 OR 兜底 MUST NOT 采用」规范性排除+虚词断裂 known-boundary 场景;evals README 基线表与 HANDOFF 分词节记录 A/B 数据与回退结论
- [x] 4.3 质量门禁复跑 + 变更归档(openspec archive)
