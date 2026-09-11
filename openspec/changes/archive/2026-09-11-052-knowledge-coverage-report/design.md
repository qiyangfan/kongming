# 052-knowledge-coverage-report 设计

## Context

知识治理面现状(均已核实):

- 表知识登记在 `rag.table_descriptions`,四维键 (tenant_id, datasource_key,
  schema_name, table_name),内容含 table_comment / columns_comment(列→
  标签 dict)/ sample_queries / alias,五态覆盖优先级
  INTROSPECTION < AI_GENERATED < MANUAL_UPLOAD < MANUAL_EDIT <
  FEEDBACK_REFINED(041/042 演进后形态)。
- 真实结构来源:`rag/repository.py::introspect_all` 与 fetch-metadata 同
  扫描集语义(default schema BASE TABLE、排除系统 schema 与保留表),返回
  内省表清单(含列名集与既有列注释)。
- few-shot 通道:`rag.query_history` 仅回写 SUCCESS(insert_history 硬编码),
  每维上限 500(QUERY_HISTORY_CAP),召回只取 status='SUCCESS'。
- 失败与反馈信号:`public.chat_message` 四态 status(SUCCESS/FAILED/
  REJECTED/NO_DATA,044 映射)+ rating(UP/DOWN)+ corrected_sql +
  feedback_status;`public.chat_session` 持 tenant_id 与 datasource_key,
  可作 (租户, 数据源) 归属过滤。feedback 域已有读 chat_message 的跨域
  只读先例。
- 042 对账已确立「内省失败不触发对账」的宁缺毋错口径。

LLM Wiki 调研裁决(对话已定):移植其 gap detection 方法论(孤立页/稀疏
社区 → 空白盘点),否决其生成式编译实现(非确定性生成物入知识面与 041
回写繁殖、051 硬凑沉淀同源)。设计含义:**报告的所有输入必须是确定性可
复核的存量数据,任何环节不得引入 LLM**。

## Goals / Non-Goals

**Goals:**

- 治理前哨:一次调用盘点四类空白,直接产出 041 标签补全与示例 SQL 治理的
  工作清单(自动化的前哨,不是自动化本身)。
- 零风险面:纯读、零 LLM、零写入、无缓存,对运行时检索/生成链路零影响。
- 领域纯净:差集/归并逻辑全部落 models 纯函数,可脱离容器确定性单测
  (宪法 Article II)。

**Non-Goals:**

- 见 proposal Non-goals(不做编译层/持久化/语义聚类/自动修复/前端页/调度)。
- 设计层补充:不为报告建任何新表、新索引、新迁移;不改 introspect_all
  扫描语义(直接复用)。

## Decisions

1. **落点 rag 域(而非 chat 域)**:报告语义是知识治理(knowledge spec 的
   实现域),chat_message 只是失败信号的输入源之一。跨域只读沿 feedback
   域先例,SQL 限于聚合查询,不写不锁。
   *替代方案*:放 chat 域(数据最近)——拒绝:会把治理工具的归属语义搞错,
   且 rag 域才是内省与登记数据的持有方。

2. **GET 端点、无缓存**:纯读聚合;内省成本与 fetch-metadata 的内省阶段同
   量级(information_schema 查询,先例已接受),且无润色 LLM 调用,远快于
   fetch-metadata。按需触发的治理工具频率低,缓存只添失效链复杂度
   (039/047 缓存失效范式的反面:不建缓存即无失效问题)。

3. **零 LLM 判定**:四类空白均可确定性判定——覆盖差集(内省集−登记集)、
   标签差集(列名集−标签键集)、沉淀统计(计数查询)、失败归并(文本
   归一化)。引入 LLM(聚类命名/空白解释)会重新打开毒知识面并增加失败
   模式,与本变更「前哨」定位相反。

4. **失败问题簇口径**:status ∈ {FAILED, REJECTED, NO_DATA} ∪ rating=DOWN
   ∪ corrected_sql 非空;归一化=折叠空白+去首尾+忽略大小写;窗口 30 天;
   封顶 50 簇。NO_DATA 是 051 判据下的**成功态**(如实弃答),但恰恰是
   「知识没接住」的直接信号(哨兵触发≈无表可支撑),故作为弱信号计入,
   状态分列呈现让治理者自行判读,不与真失败混计。
   *替代方案*:仅计 FAILED/REJECTED——拒绝:会漏掉最大的一类知识空白信号。

5. **内省失败整体失败**:半份报告(只有失败簇、没有覆盖节)会被误读为
   「登记完备」,与 042「内省失败不触发对账」同一宁缺毋错口径。

6. **不加审计写**:只读操作;fetch-metadata 记 sys_oper_log 因其为管理写,
   本端点不改变既有审计口径。

7. **封顶与计数分离**:清单条目封顶(防响应体失控),总数计数如实(保盘点
   语义)。封顶值取工程经验值,契约固化后前端可依赖。

8. **契约(ADR-005)**:响应模型入 OpenAPI,`make contracts` 重新生成,
   contracts/openapi.json 与前端类型随代码提交;字段英文、文案值中文,
   与既有契约风格一致。

Schema 核对(specs 规则):报告所需全部列均已存在于 baseline
(table_descriptions.columns_comment/sample_queries/source_type、
query_history.status、chat_message.status/rating/corrected_sql、
chat_session.tenant_id/datasource_key),**无迁移**(ADR-004 无涉)。

## Risks / Trade-offs

- [大库内省耗时拉长响应] → information_schema 查询与 fetch-metadata 内省
  同量级先例;无 LLM,耗时上限远低于 fetch-metadata;不优化,等实测超标
  再议(不过早优化)。
- [归一化去重漏并同义问题(「每个门店库存」≠「各门店库存数」)] → 接受:
  v1 精确归一宁漏勿错,语义聚类是明确 Non-goal;漏并只影响呈现,不影响
  正确性。
- [NO_DATA 弱信号噪音(负例评估题也会计入)] → 状态分列+治理者判读;
  报告是给人看的工作清单,不是自动动作触发器。
- [chat_message 聚合查询随消息量增长变慢] → 30 天窗口+按 (tenant,
  datasource_key) 过滤;必要时后续加时间维度索引(独立演进,本变更不动
  schema)。
- [报告被误当自动化修复的输入直接执行] → 契约与文档写明只读盘点;自动
  修复(生成标签/few-shot)属被否决路径,须走独立提案论证。

## Migration Plan

纯新增端点:部署即生效,无数据迁移、无开关。回滚 = 移除路由并回退契约
产物(契约门禁保证两侧一致)。上线后首次冒烟对 default 源执行,核对四类
空白呈现与已知缺口(如 sakila_ext 类零注释列)相符。

## Open Questions

(无——封顶值/窗口期均为可调工程参数,契约固化后如需调整走正常变更。)
