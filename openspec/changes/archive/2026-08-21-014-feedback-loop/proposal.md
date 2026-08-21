# 014 反馈闭环(feedback-loop)

## Why

对话问答闭环(roadmap 第一梯队)的最后一块产品缺口:用户看到错误答案只能
重问,无法表达「不对」、无法给出正确 SQL 让系统学习。feedback 三域 spec
(feedback 反馈提交/feedback-refine 处置精修/feedback-analytics 统计分析)
已在第一梯队平移入库,baseline 已按 ADR-004 一次建全 chat_message 反馈
8 列+部分索引,009 已铺好 query_history 回写通道与 FEEDBACK_REFINED 覆盖
优先级枚举位——本变更把整条「用户反馈 → 处置 → 纠错沉淀 few-shot →
知识精修」链路接通,问答质量自此有了自我改进的产品抓手。

## What Changes

**反馈提交(feedback 域)**:
1. `POST /chat/messages/{messageId}/feedback`(新权限点
   `chat:feedback:submit`,排序 6,DEVELOPER 推荐绑定):rating/comment/
   correctedSql;归属 fail-closed 403 同形;仅 assistant 消息;重复幂等
   覆盖(null 保持/显式空串清空/行锁串行);操作日志 feedback.submit。
2. 纠错 SQL 沉淀:correctedSql 过 SQL 防火墙(只读约束同主链路,校验
   失败整体拒);通过且与现值不同时,取同会话该消息前最近 user 问句,
   复用 009 writeback 通道写 rag.query_history;embedding/写入失败不
   阻断反馈;清空纠错不删已沉淀行;**不回写 table_descriptions**(不变式)。
3. messageId 打通:实时问答响应补 assistant_message_id/datasource_key
   (纯增字段);历史消息回显补 rating/corrected_sql 字段。

**处置与精修(feedback-refine 域)**:
4. `PUT /feedback/{messageId}/status`(新权限点 `system:feedback:handle`,
   排序 8,仅 SUPER_ADMIN):枚举校验;handler/handle_time/note 落库;
   note 语义同提交(null 保持/空串清空);操作日志 feedback.handle。
5. `GET /feedback/{messageId}/involved-tables`(`system:feedback:view`):
   sql_text+corrected_sql sqlglot 抽表名,按 tenant+datasource+table 匹配
   知识库条目(hasKnowledge/knowledgeId);解析失败兜底 parseError。
6. 前端反馈详情弹窗:涉及表区(「编辑知识」跳知识库)+处置区(无 handle
   权限隐藏)。

**统计分析(feedback-analytics 域)**:
7. `GET /feedback/overview` + `GET /feedback/page`
   (`system:feedback:view`,排序 7,仅 SUPER_ADMIN):days≤90 默认 30;
   Asia/Shanghai 固定时区分桶逐天补零;行级可见性(非 ALL 强制本人,
   SUPER_ADMIN 种子即 ALL——spec 立约按数据范围判定,不硬绑角色);
   明细分页 feedbackStatus 筛选(refine 域增补)。
8. 前端 /admin/feedback 第六子页:统计卡+趋势折线+筛选区+明细分页表
   (处置状态列/筛选)+详情弹窗;页面只读(处置在弹窗内)。

**web 赞踩交互**:assistant 消息操作行赞/踩(lucide 图标);赞立即提交;
踩弹窗收集 comment/correctedSql(预填已提交值或当前 SQL,可清空);
已反馈回显状态。

**rag 搭车(009 挂账清零,同动 rag 写链路一并对齐)**:
9. 内省列注释进润色链路(009 挂账⑦:_introspect_all_on 补 col_description,
   enriched 块注释质量提升)。
10. reindex 先润色后删写(009 挂账⑪:消 delete→commit 长事务锁窗口,
    并发 upload 不再阻塞)。

## Capabilities

### New Capabilities

(无——三域 spec 已平移入库,本变更是实现既有立约)

### Modified Capabilities

- `feedback`: 既有 requirements 精化改写为 Kongming 实现口径(端点路径/
  权限点排序/403 同形/审计动作/幂等覆盖三态/沉淀口径;R1 后增补
  comment 回显字段与「重复提交清空处置三列」)。
- `feedback-refine`: 同上(处置写接口/分页处置状态筛选/涉及表定位/
  详情弹窗;R1 后 parseError 改「部分失败跳过」措辞)。
- `feedback-analytics`: 同上(统计概览/明细分页/行级可见性/前端页面)。
- `chat`: 「问答查询端点」响应模型纯增 messageId/datasourceKey 字段
  (spec 已立约 messageId 打通,chat 域 delta 补响应字段声明)。
- `knowledge`: 「元数据抓取」requirement 增补——内省列注释(col_description)
  进入润色输入;「重建索引」requirement 修订——先润色后删写的执行序。

## Non-goals

- 不做反馈驱动的 table_descriptions 自动精修(FEEDBACK_REFINED 自动写
  知识库)——spec 不变式明确纠错只进 query_history;精修仍经人工编辑/
  上传链路(详情弹窗「编辑知识」跳转已打通)。
- 不做 query_history 治理 TTL/去重(009 挂账③,随 eval 域)。
- 不做反馈数据的导出/审计查询页(随 audit 域)。
- 不做处置通知/工单流转(无消息队列,技术栈锁定)。
- 不动问答主链路、审计、导出、EXPLAIN、RAG 召回逻辑(spec 不变式)。

## Impact

- **schema**:零变更(反馈 8 列+索引 0001 baseline 已建全,ADR-004 兑现)。
- **api**:`kongming/feedback/` 新域包(router/service/models/ports/
  repository,宪法分层);chat 域加反馈提交端点(挂 chat router 前缀)
  与响应模型纯增字段;rag 域内省补 col_description、reindex 执行序;
  种子 3 权限点(排序 6/7/8)+ DEVELOPER 推荐绑定 chat:feedback:submit;
  import-linter 契约补 feedback 域零框架。
- **agent**:零改动(纠错沉淀复用 api 侧 writeback;润色输入增列注释
  不改变 describe-table 契约形态——payload 既有 comment 字段内容更富)。
- **web**:assistant-card 赞踩操作行+点踩弹窗;/admin/feedback 第六子页
  (统计卡/趋势/筛选/分页/详情弹窗);AdminLayout 子导航注册
  (perm=system:feedback:view,与路由 PermRoute 同源)。
- **契约**:`make contracts` 重生成(5 新端点+chat 响应纯增字段)。
- **ADR 引用**:ADR-002(纠错沉淀复用 api 侧通道,agent 零改动)、
  ADR-004(零新迁移,baseline 列已建全)、ADR-005(契约重生成产物提交)。
