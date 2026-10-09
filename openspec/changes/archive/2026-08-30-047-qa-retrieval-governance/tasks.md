# Tasks: 047 问答链路检索治理与多轮质量修复

## 1. 视图不入知识(knowledge / internal-meta)

- [x] 1.1 测试先行:TEST_DB 建基表+视图,断言 `introspect_all` 仅返回基表
      (rag repository 层用例)
- [x] 1.2 `rag/repository._introspect_all_on` 内省 SQL 收窄 `BASE TABLE`
- [x] 1.3 `internal/meta` 内省同口径收窄;两处用例齐
- [x] 1.4 spec delta:knowledge「扫描对账」收录范围 + internal-meta「租户
      schema 实时内省」同步

## 2. 多轮召回历史继承(nl2sql-agent / rag)

- [x] 2.1 测试先行:recall 假端口用例——继承路去重合并、主路优先、总量
      封顶、history 空时与现状同形、缓存键随历史分叉
- [x] 2.2 `RagService.recall(history)` 双路合并实现;`RetrieveIn.history`
      契约字段(≤3×2000)
- [x] 2.3 agent `apiclient.rag_retrieve` 增 history;`retrieve` 节点透传
      `state["history"][-3:]`
- [x] 2.4 spec delta:nl2sql-agent「Schema 智能选取」多轮继承场景

## 3. 文档分流多轮门槛(nl2sql-agent)

- [x] 3.1 测试先行:`route_after_retrieve`——多轮+仅「是什么」→generate;
      多轮+强词→doc;单轮+「是什么」→doc(024 语义钉住)
- [x] 3.2 `DOC_STRONG_INTENT_WORDS` + 路由条件实现
- [x] 3.3 spec delta:nl2sql-agent「文档通道与知识分流节点」场景

## 4. 自愈喂回分类(nl2sql-agent)

- [x] 4.1 测试先行:`_selfheal_guidance` 三分类命中/优先级/未命中空串
- [x] 4.2 generate `extra` 组装接入分类(替原 blanket does-not-exist 分支)
- [x] 4.3 spec delta:nl2sql-agent「selfheal 错误分类」喂回场景补记

## 5. 建议缓存治理级联失效(chat-suggestions / knowledge)

- [x] 5.1 测试先行:五条知识写路径触发 `suggestion_evictor`(假端口),
      未注入(缺省 None)时零回归
- [x] 5.2 `rag/ports.SuggestionCacheEvictor` 协议 + `RagService` 注入与
      五写路径接入(与 ctx evict 同点、同事务)
- [x] 5.3 两处 `get_rag_service` 组合根绑定 `SuggestionRepository` 适配
- [x] 5.4 spec delta:chat-suggestions「缓存生命周期」+ knowledge 写路径
      级联条款

## 6. verdict 文案与 error_detail(chat / web)

- [x] 6.1 测试先行:blocked_friendly RESERVED_TABLE 新文案;FAILED
      error=友好文案 且 error_detail=原始错误;NO_DATA/REJECTED 其余
      分支不动
- [x] 6.2 `ChatQueryOut.error_detail` 纯增 + `_outcome` 组装 + result_json
      落库;`make contracts` 生成前端类型
- [x] 6.3 web `assistant-card`:FAILED 友好文案 + errorDetail 折叠
      「技术详情」;tsc/eslint 绿
- [x] 6.4 spec delta:chat「问答查询端点」error_detail 与文案场景

## 7. dev 环境数据修复与全量验证

- [x] 7.1 重启 api/agent 载新代码;admin 经 API 对 default 与 sakila_dyn
      重跑 fetch-metadata(对账清偿视图+user_memory 存量,级联失效建议
      缓存);SQL 复核 table_descriptions 与 chat_suggestion 终态
- [x] 7.2 `make check` 全绿(api+agent+web)
- [x] 7.3 浏览器实测:default 源建议 chips 重生成(无撞墙题)✓;多轮追问
      走 SQL 通道 ✓;正常链路不劣化 ✓;FAILED 呈现未能自然触发(017 转绿),
      改由 api 契约测试钉 error_detail 行为,折叠组件为纯条件渲染(存量档
      缺省路径已覆盖)

## 8. 评估重立基线(eval)

- [x] 8.1 清 ctx 缓存后重跑 smoke/full/multiturn;对比并记录
- [x] 8.2 evals/README 基线表追加 047 行;纪律段补「知识/文档变更后须
      重跑评估」;注记 08-23 环境混杂教训
- [x] 8.3 设计/任务核对,活动变更收尾(归档待指令)（本变更已归档于 archive/2026-08-30-047-qa-retrieval-governance,2026-10-09 快照清点时补勾）
