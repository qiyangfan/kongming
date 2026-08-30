# Design: 047 问答链路检索治理与多轮质量修复

## D1 视图不入知识(收录层收窄)

- `rag/repository._introspect_all_on` 与 `internal/meta` 内省 SQL:
  `table_type IN ('BASE TABLE','VIEW')` → `table_type = 'BASE TABLE'`(两处
  同口径,后者服务 ReAct 内省回退与元数据通道)。
- 存量清偿走 042 既有对账:收录集收窄 → keep 集不含视图/保留表 →
  `delete_stale_entries` 删自动来源孤儿;MANUAL_* 孤儿照旧保留+告警
  (人工裁决语义零改动)。
- 传播面确认:recall 三路(向量/全文/历史)只读 table_descriptions,收录
  收窄后自动干净,召回层零改动;041 列标签 `column_comments` 对视图列
  回退物理名(宁缺毋错,既定原则)。
- dev 数据修复(实施期操作,非代码):admin 经 API 对 default 与 sakila_dyn
  重跑 fetch-metadata,清偿 4+4 视图条目与 public.user_memory 存量 2 行
  (045 前存量,内历已滤但对账未跑)。

## D2 多轮召回历史继承

- 契约:`RetrieveIn.history: list[str]`(新增,≤3 条、单条 ≤2000,与
  ChatQueryIn.history 同限);`RagService.recall(..., history: list[str] | None)`。
- 合并算法(确定性,不引新概念):
  1. 主路:现问句向量+全文 → RRF 融合(现状);
  2. 继承路:取 history 末条(更早轮次语义已被 understand 改写并集),
     独立向量+全文 → RRF;
  3. 合并:主路序在前,继承路按序追加其中主路未含的 (schema,table),
     总量封顶 `2 × RECALL_TOP_K`;
  4. history_sql(few-shot)仍只用现问句向量(继承路问句的成功 SQL 语义
     已由表集继承覆盖,不再扩 few-shot 面)。
- 缓存:`ctx` 键材料由 `question` 改为 `question + "\x1f" + history 末条`
  (缺省空串与现状同键);`ContextCache` 签名不动(键材料作为 question
  参数传入,派生逻辑留在 cache 内部)。
- 透传链:agent `retrieve` 节点取 `state["history"][-3:]` → apiclient
  `rag_retrieve(..., history=...)` → api 内部端点 → recall。history 为空
  (单轮)时全链路与现状逐字节同形(零回归)。
- ReAct 工具适配器(`_RagServiceAdapter`)不传 history(Non-goals 已声明)。

## D3 文档分流多轮门槛

- `DOC_STRONG_INTENT_WORDS = KNOWLEDGE_INTENT_WORDS 去掉「是什么」`
  (「为什么」保留:解释型意图天然指向文档)。
- `route_after_retrieve`:命中知识意图后,若 `history` 非空且**仅**泛系
  系词命中(强词零命中)→ 返回 generate。单轮行为逐字不变(024 语义);
  强词命中任何之一照旧分流。
- 不引入指代词表(036 两犯教训:弱信号词表不可维护);结构性条件
  (历史在场)替代内容判定。

## D4 自愈喂回分类

- `nodes._selfheal_guidance(error: str) -> str` 纯函数(优先级自上而下):
  1. `missing FROM-clause entry for table "X"` → 「补 X 的 JOIN 或改用已
     定义别名;不得引用『可用表结构』之外的表」;
  2. `column "..." ... does not exist` → 「列不在清单——只用清单内列并核对
     拼写;缺口径列不得近似顶替」(046 列级哨兵的喂回镜像);
  3. `relation "..." ... does not exist` → 「表不在清单——核对清单;确无
     数据只输出 NO_RELEVANT_TABLE」(043 既有出口,收窄到关系类);
  4. 其余 → 空串(维持现状泛化喂回)。
- `generate` 组装 `extra` 时以分类指令替代现 blanket
  `"does not exist" in error` 分支(该分支会把列错也引向表哨兵,误导)。
- 节点内 user 拼接属自研资产(notes:SQL_GEN_SYSTEM 系统模板零改动,
  prompt 资产纪律不适用)。

## D5 建议缓存治理级联失效

- `rag/ports.py` 新协议 `SuggestionCacheEvictor`(`evict_by_datasource`,
  与 datasource 域同名端口同形);`RagService` 构造注入可选
  `suggestion_evictor`。
- 触发点:五条知识写路径(upload_blocks/edit_table/delete_table/
  fetch-metadata `_ingest`/reindex)在 `self._cache.evict`(ctx 缓存)同点
  调用——与 ctx 失效同事务(删除语句落同一会话,rag commit 一并提交),
  原子性同 039 datasource 级联先例。
- 组合根:chat/router 与 rag/router 两处 `get_rag_service` 绑定
  `SuggestionRepository(session).delete_by_datasource` 适配(lambda 闭包
  承担 commit 语义注释——delete 在 rag 事务内,由 rag commit 收口)。
- 失败语义:DELETE-by-key 无外键指向、失败概率趋零;若真失败,事务中断
  使 fetch 显式失败(不做静默吞——与 datasource 先例一致,同事务原子)。

## D6 verdict 文案与 error_detail 契约

- `blocked_friendly` RESERVED_TABLE 分支 →
  「该问题涉及的数据不允许查询,请调整问法或咨询管理员」(中性、不责备、
  可行动;GUARDRAIL/DATA_SCOPE/写操作分支语义准确,不动)。
- FAILED 分支:`error = "查询执行失败,请调整问法后重试"`;
  `error_detail = error_raw`(新契约字段 `ChatQueryOut.error_detail:
  str | None = None`,纯增;result_json 落库同步携带,流式 result 帧=
  ChatQueryOut 全量自动跟随)。
- web:`assistant-card` FAILED 分支渲染友好 error;`errorDetail` 非空时
  折叠 `<details>`(「技术详情」)展示原始错误——honest + 可排障 + 不吓人。
  类型经 `make contracts` 生成,禁手写(ADR-005)。
- 存量落库消息不迁移(Non-goals);`_to_message` 回显缺省 None 即隐藏
  折叠块。

## D7 评估重立基线

- 修复全量合入后:清 ctx 缓存 → smoke/full/multiturn 三跑 → README
  基线表追加行(标注 047);纪律段补「知识库收录/对账/AI 再生成、文档
  增删后须重跑评估」;注记 08-23 知识再生成与文档灌入晚于 08-22 基线
  的混杂教训(视图失真、维度表挤占两形态均已随本轮修复消除,新基线
  即干净环境基线)。

## 测试策略

- 测试先行项:D3(路由门槛)、D4(喂回分类)为行为规则类;D1(收录
  收窄)、D2(历史继承)、D5(级联失效)、D6(文案+契约)随实现即测,
  不属安全规则类但同样先写失败用例钉行为。
- D1 用 TEST_DB 真建表+视图断言内省排除;D2 服务级假端口断言合并序与
  封顶、缓存键分叉;D5 断言五写路径触发 evict;D6 断言三态映射与
  error_detail 携带;web 层 tsc/eslint 门禁+浏览器实测。
