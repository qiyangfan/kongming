# Tasks: add-retrieval-quality-eval

## 1. 指标纯函数(测试先行)

- [x] 1.1 先写失败测试 `apps/api/tests/test_retrieval_eval_metrics.py`:Recall@k 边界(gold 空集/全命中/部分命中/k 截断)、全跳覆盖率、gold 片段→父块映射(片段唯一/跨块首含/片段缺失报错)(验证:pytest 该文件红)
- [x] 1.2 实现 `apps/api/evals/retrieval_metrics.py` 纯函数(Recall@k、全跳覆盖率、gold 映射),不依赖评估环境(验证:1.1 转绿)
- [x] 1.3 跑 api 测试子集确认绿(验证:pytest tests/test_retrieval_eval_metrics.py)

## 2. document-rag 试跑端点

- [x] 2.1 先写失败测试(仿 test_rag_admin 夹具风格):非超管 403、逐路命中结构、与主检索同源 top-k、重复调用零副作用、伪造 datasource_key 同形拒绝(验证:pytest 该文件红)
- [x] 2.2 从 `documents_service.retrieve_docs` 提取逐路命中共享形态(向量/全文各路原生分+融合名次),主检索行为零回归(验证:既有 test_document_rag/test_rag_recall 全绿)
- [x] 2.3 实现 `GET /system/document/retrieval-probe`(权限复用 system:document:manage,零副作用)(验证:2.1 转绿)
- [x] 2.4 跑 `make contracts` 再生契约产物(ADR-005)(验证:contracts/openapi.json 与 api-types.gen.ts 已更新)
- [x] 2.5 api 测试子集绿(验证:pytest apps/api/tests 相关文件)

## 3. evals harness retrieval 层

- [x] 3.1 编写 `evals/retrieval/` 种子语料(≥4 份 md,含跨文档关联事实)与 `retrieval_eval.yaml`(≥12 题,multi-hop ≥5,含评测集自检下限)(验证:语料片段自检通过)
- [x] 3.2 评测 runner:登录→语料幂等上传→逐题调试跑端点→指标纯函数评分→逐题明细+分类聚合输出,单题失败不中断(验证:真栈冒烟)
- [x] 3.3 pytest 标记 retrieval 注册(仿 smoke/full/multiturn 分层),默认收集隔离(验证:无评估环境默认收集不含检索评测)
- [x] 3.4 本地真栈跑通 retrieval 层完整一轮(验证:pytest evals -m retrieval 输出聚合分)

## 4. 基线与门禁

- [x] 4.1 README 基线表新增检索档并记录首轮基线(标注题量与 k)(验证:README 行存在)
- [x] 4.2 eval-nightly 工作流纳入 retrieval 层(验证:workflow 文件包含该层)
- [x] 4.3 全套 make check(验证:api/agent/web 三侧绿;contracts 未提交树机制性红按既知口径记录)
