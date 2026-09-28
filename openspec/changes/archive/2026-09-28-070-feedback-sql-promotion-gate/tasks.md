# 070-feedback-sql-promotion-gate 实施任务

## 1. 测试先行

- [x] 1.1 提交 correctedSql 后状态为 PENDING、反馈字段与审计正常，且 query_history 不新增或启用该 SQL
- [x] 1.2 SQL 防火墙拒绝仍整体回滚；既有执行样本与反馈样本状态不变
- [x] 1.3 重复提交将反馈重置为 PENDING，并停用 messageId 关联的旧样本；null/空串三态合并语义保持
- [x] 1.4 ADOPTED + 非空 correctedSql 幂等晋升；ADOPTED + 空 SQL 不创建样本
- [x] 1.5 PENDING/REJECTED/DISMISSED 均不召回；从 ADOPTED 转出后样本停用
- [x] 1.6 embedding 晋升失败返回 503，反馈状态、审计和 query_history 无部分提交
- [x] 1.7 同问句执行样本与反馈样本并存；召回只返回优先样本且最多一条
- [x] 1.8 tenant + datasource 隔离、缓存精确失效及缓存失效故障降级均有覆盖

## 2. Schema 与存量迁移

- [x] 2.1 新建 Alembic 迁移：query_history 来源类型、反馈 messageId、召回启用状态和必要 CHECK/索引；不改 baseline
- [x] 2.2 将原唯一索引改为仅约束执行成功样本；新增反馈 messageId 幂等索引，覆盖同问句异来源并存
- [x] 2.3 对存量行做精确来源恢复：匹配成功对话的行恢复为执行样本；精确匹配当前反馈的行按处置状态恢复；其余标记 LEGACY 并停用
- [x] 2.4 迁移测试覆盖 UUID v7、约束、索引、来源恢复、歧义隔离和 downgrade 行为
- [x] 2.5 Apply 记录迁移前后总量、执行样本、ADOPTED 反馈样本、LEGACY 停用行数量
      **实测(2026-09-28,一次性库 kongming_070,0033 态播种 4 行存量)**:
      迁移前(0033):query_history 总量 4(全部 EXECUTION 语义,召回态隐含)。
      迁移后(0034):EXECUTION 启用×1(问句A,有成功对话佐证)/ FEEDBACK
      启用×1(问句B,corrected_sql 匹配且 feedback_status=ADOPTED,linked=t)/
      FEEDBACK 停用×1(问句C,PENDING 审核中不可召回)/ LEGACY 停用×1
      (问句D,无任何对话佐证的孤儿样本)。
      带数据往返:downgrade 0034→0033 干净(4 行保留,列撤除);再 upgrade
      重放,分类结果逐行复现(1/1/1/1 不变,治理 SQL 幂等确定)。
      容器现网库(kongming)迁移同步于 2026-09-28 05:11 完成
      (0031→0034 全链,存量 LLM 用量 420 行零影响)。

## 3. 反馈处置与 RAG 实现

- [x] 3.1 feedback 域经窄 Protocol 调用样本启停能力；领域 service 不直接依赖 SQLAlchemy/Redis
- [x] 3.2 提交路径保留现有校验和反馈行为，移除即时 query_history 回写；重提时停用旧反馈样本
- [x] 3.3 处置路径将样本启停与反馈状态、操作日志纳入同一数据库事务
- [x] 3.4 RAG 执行成功回写显式标记来源，保持现有同问句覆盖语义
- [x] 3.5 RAG 历史路仅召回可用来源，按问句优先选最近采纳的反馈样本，并在无反馈样本时回退执行成功样本
- [x] 3.6 晋升或停用提交后复用 ContextCache 按租户+数据源精确失效

## 4. 契约与收口

- [x] 4.1 处置端点声明 embedding 晋升失败的 503 错误；请求与成功响应字段保持不变
- [x] 4.2 执行 make contracts，提交 OpenAPI 与生成类型产物；确认无非预期契约漂移
- [x] 4.3 更新 feedback、feedback-refine、rag spec delta 与相应文档
- [x] 4.4 手工冒烟：提交待审→检索不可见→采纳后可见→驳回/重提后不可见；同问句执行样本不被覆盖
      **实测(2026-09-28,现网容器栈 api:18080/agent:18081+冒烟库)**:
      ① 对真实助手消息提交纠错(附修正 SQL)→ feedback_status=PENDING;
      query_history 无反馈样本(仅原 EXECUTION 行)——待审不可召回。
      ② 采纳(ADOPTED)→ 同事务晋升:query_history 出现 FEEDBACK 行
      recall_enabled=t、feedback_message_id 关联、sql_text=修正 SQL。
      ③ 内部 retrieve 端点(/internal/rag/retrieve,与主链路同源)返回
      的 history_sql 正是修正 SQL——采纳后召回可见。
      ④ 以新修正 SQL 重提→ 旧 FEEDBACK 样本 recall_enabled=f
      (状态回 PENDING);retrieve 回落原执行样本——重提后不可见,
      且同问句执行样本行全程保留未被覆盖。全链路与设计一致。
- [x] 4.5 运行 make api-check 与 make check，记录结果和受影响的评估集对拍
      **结果(2026-09-27 最终门禁轮)**:api 1024 passed(83% 覆盖,ruff+mypy+
      pyright+lint-imports 全净);agent/liannu/web/contracts 同轮全绿。
      评估集对拍(070 对召回优先语义的影响)依赖 065 评估门禁+真实语料,
      标记【待确认-环境限制】: Recall 优先逻辑已有单测覆盖(test_recall_
      prefers_feedback_sample),端到端对拍待评估环境就绪后执行。

> 070 落地记录(2026-09-27):
> - 迁移 0034_feedback_sql_promotion_gate(接 0033 之后):query_history 增
>   source_type(EXECUTION|FEEDBACK|LEGACY)/recall_enabled/feedback_message_id;
>   原唯一索引收窄为部分索引(仅约束执行样本)+ 反馈 messageId 幂等索引 +
>   召回过滤索引;存量治理=能匹配成功对话的证为 EXECUTION、能匹配当前反馈
>   的按处置状态恢复(ADOPTED 启用)、其余标 LEGACY 停用(迁移 SQL 静态,
>   测试库 up/down 往返实证)。
> - rag 域:recall_history 改为候选查询(仅 EXECUTION|FEEDBACK 且启用态,
>   多取 3 倍候选)+ service 同问句去重(优先 ADOPTED 反馈样本,≤K 条);
>   insert_history 显式 EXECUTION + 部分索引冲突目标;promote_feedback_sample
>   /disable_feedback_samples(与处置同事务,不自行 commit);evict_ctx 公开出口。
> - feedback 域:submit 移除即时沉淀(070 提交零写入——对齐既有 3 处测试
>   断言至新契约);重复提交(现值 ADOPTED)同事务停用旧样本;handle:
>   ADOPTED+非空 correctedSql → 同事务晋升(embedding 失败 → 503 整体回滚),
>   其余状态 → 同事务停用;晋升/停用提交后 ctx 缓存按租户+数据源精确失效。
>   窄端口 FeedbackSampleGate(领域零框架依赖)+ 组合根实现(同请求会话)。
> - 契约:handle 端点 503(SAMPLE_PROMOTION_UNAVAILABLE)显式声明;make
>   contracts 重新生成;前端 typecheck/lint 绿(本变更无 web 改动)。
> - 测试:tests/test_feedback_promotion.py 11 例(提交零沉淀/ADOPTED 幂等
>   晋升/空 SQL 不建样本/三态不召回/转出停用+重启/重提停用/同问句双源
>   并存/召回优先反馈样本/503 无部分提交(状态+样本+审计三查)/缓存失效/
>   防火墙原子复核);api 全门禁绿:996 passed,ruff+mypy+pyright+lint-imports。
> - 【待确认-环境限制】2.5 apply 迁移计数(前后总量/执行/ADOPTED 反馈/
>   LEGACY 行):本环境为全新测试库(存量=0,治理语句零命中),计数须在目标
>   部署环境执行迁移时采集;治理逻辑已由迁移 SQL 与 up/down 往返覆盖。
>   4.4 手工冒烟(提交待审→不可见→采纳后可见→驳回后不可见)需在跑服务,
>   自动化层已由全栈测试等价覆盖。
