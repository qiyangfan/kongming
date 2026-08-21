# 014 反馈闭环 — 设计

## Context

动机与范围见 proposal.md。实现期已核明的现状事实:

- **chat_message 反馈 8 列+部分索引在 0001 baseline 已建全**(rating/
  feedback_comment/corrected_sql/feedback_time/feedback_status/
  feedback_handler/feedback_handle_time/feedback_handle_note,
  CHECK 枚举+idx_chat_message_feedback),零迁移(ADR-004 兑现)。
- **009 回写通道**:`RagService.writeback(tenant_id, datasource_key,
  question, sql_text)`(chat 域经 `QueryHistoryWriter` 端口消费)——纠错
  沉淀直接复用,embedding/降级/审计语义全继承。
- **message_id 缺口**:ChatOutcome/ChatQueryOut 无 message_id;
  `insert_message` 返回 mid 但 service 未透传;历史消息 SessionMessage
  已有 id(010 EXPLAIN 已消费),行数据 list_messages 未 SELECT
  rating/corrected_sql。
- **reindex 现状**(rag/service.py:172):delete_all 与后续润色/upsert 同
  一 session 同一事务——分钟级 LLM 润色期间 delete 未提交,并发 upload
  撞四元唯一键阻塞(挂账⑪实证形态)。fetch-metadata 的 `_ingest` 不存在
  该问题(无长 delete),只需 reindex 单独重构。
- **内省现状**(rag/repository.py `_introspect_all_on`):表注释
  (obj_description)已查,列注释(col_description)未查(挂账⑦)。
- **datascope**:most_permissive 是纯函数(datascope/models);feedback
  只需「是否 ALL」判定,repository 一条 SQL 收口,零跨域依赖。
- **权限点种子**(auth/seed.py):现 5 个(排序 1-5);SUPER_ADMIN 自动
  覆盖全部注册点,DEVELOPER 推荐绑定清单只增不摘。

## Goals / Non-Goals

**Goals:**
- feedback 三域 12 个 requirement 全部落地(6 端点+3 权限点+赞踩 UI+
  分析页+详情弹窗),spec 不变式(主链路/召回/知识库零改动)门禁可证。
- 009 挂账⑦⑪ 清零(列注释进润色、reindex 先润色后删写)。

**Non-Goals:**
- 不做 FEEDBACK_REFINED 自动写 table_descriptions(spec 不变式明禁)。
- 不做 writeback 异步化(009 挂账⑤,同步 after-commit 与 009 同形态)。
- 不做 query_history 治理(随 eval 域)。

## Decisions

### D1. feedback 域包独立,双前缀路由

`kongming/feedback/`(models/ports/service/repository/router,宪法分层,
import-linter 契约补 models/service/ports 零框架)。router 注册两条
APIRouter:`/chat` 前缀(仅 `POST /messages/{messageId}/feedback`,
路径归属 spec 既定)与 `/feedback` 前缀(status/involved-tables/
overview/page 四端点)。归属/行级可见性查询 repository 自备
(消息 JOIN 会话;`SELECT bool_or(data_scope='ALL')` 角色集判定——
不 import datascope,与 008 options 复校自备先例同构)。
chat router 零改动(反馈端点不属于 chat 域用例)。

### D2. 幂等覆盖 = 行锁读现值 + 覆盖写

repository.submit:`SELECT ... FOR UPDATE` 锁消息行读现反馈值 →
UPDATE 覆盖(rating/feedback_time 恒最新;comment/corrected_sql
null 保持、显式值覆盖、空串清空——三态语义在 service 层判定后传
COALESCE/CASE 形态 SQL)。并发同一消息由行锁串行(spec 立约)。
重复相同 correctedSql:service 比对现值相同则跳过沉淀(反馈列照盖)。

### D3. 纠错沉淀 = 防火墙校验复用 + 提交后同步 writeback

correctedSql 非空先过 sqlfw.check(只读根)+ check_reserved_tables +
check_schema_scope(租户 schema 边界,tenant_key 取会话)——与 internal
_prepare 初检段同一规则集,不执行不改写;任一拒绝→400 整体拒(不落
反馈列)。校验通过且与现值不同:反馈事务 commit 后,取同会话该消息前
最近 user 问句(created_at DESC LIMIT 1;无问句不沉淀),经
QueryHistoryWriter.writeback 同步沉淀;失败 try 告警不阻断(与 009
chat 回写同形态,spec「沉淀写入失败不阻断反馈」)。
datasource_key=会话快照(chat_session.datasource_key)。

### D4. message_id 透传 = ChatOutcome 纯增字段

ChatOutcome 补 `message_id: str | None`(service 在 assistant 落库段
收 insert_message 返回的 mid;AgentUnavailable 分支同理;不落库路径
persist=False 恒 None)+ `datasource_key: str`(执行快照);
ChatQueryOut/_out 同增。历史消息:repository.list_messages SELECT 补
rating/corrected_sql 两列,_to_message 透传(user 消息恒 None)。

### D5. 行级可见性 = 服务端强制覆盖

service._visibility_user_id(actor):角色集含 ALL → None(不限);
否则 → actor.id(强制本人)。overview/page 的用户筛选参数经此收口
(spec「userId 仅 ALL 时生效,非 ALL 强制覆盖」)。

### D6. 统计 SQL = AT TIME ZONE 分桶 + generate_series 补零

overview:`date_trunc('day', feedback_time AT TIME ZONE 'Asia/Shanghai')`
分组聚合 up/down;`generate_series(起始日, 今日, '1 day')` LEFT JOIN
补零;days 默认 30 上限 90(传输层 ge=1 le=90)。trend day 以
Asia/Shanghai 日历日呈现(YYYY-MM-DD 字符串)。

### D7. 涉及表抽取 = sqlglot 遍历 + 末段名小写

service.involved_tables:对 sql_text/corrected_sql 各 sqlglot.parse_one
(postgres 方言),遍历 exp.Table 取末段名小写折叠(与防火墙同一语义),
CTE 名排除(exp.CTE 别名集);解析异常(SqlglotError)→ parseError=true
兜底。匹配:repository 按 (tenant_id, datasource_key, table_name) 查
rag.table_descriptions(不纳入 schema 名——知识条目按 default_schema
单 schema 内省,反馈 SQL 限定名任意;delta 已立约)。

### D8. 列注释进润色 = 内省值语义升级(零签名变化)

`_introspect_all_on` 补 col_description 查询(pg_class+pg_attribute
JOIN),组装 `columns_comment[name] = db_comment or data_type`——
有 DB 注释的列以注释为说明,无注释列行为逐字不变。
下游三消费方零改动自动获益:describe payload 的 comment 更富
(_looks_like_type 对中文注释判非类型,comment 通道直通 LLM)、
build_embedding_text 含注释(召回质量增)、INTROSPECTION 降级落库
带真实内省注释(非 LLM 臆造,spec「纯结构」语义安全,knowledge
delta 已立约「数据库既有列注释进入润色输入作参考」)。
TableBlock/IntrospectedTable/agent describe-table 契约全不变。

### D9. reindex 先润色后删写

reindex 重构,不再复用 `_ingest`(reindex 全删无需优先级判定):
①introspect_all(LookupError→DatasourceInvalid);②润色全部
(gather,与 _ingest 同并发);③embed_quietly;④**短事务**:
delete_all_for_datasource → 逐表 upsert → log_oper → commit;
⑤缓存失效。润色期零 DB 写事务挂起;①②③任一步骤异常则不进入
删写,既有条目保留(spec delta「先润色后删写」scenario)。
fetch-metadata 的 `_ingest` 流程不动。

### D10. 前端 = assistant-card 赞踩 + /admin/feedback 第六子页

- assistant-card 操作行:赞/踩 ghost 图标钮(lucide ThumbsUp/
  ThumbsDown);msg.id 缺失不渲染(spec);已反馈(rating 字段)回显
  激活态;赞=立即提交;踩=FeedbackDialog(shadcn Dialog:comment
  textarea+correctedSql textarea 预填 msg.sql,可清空);提交中禁用
  (与删除对话框 busy 同范式);无 chat:feedback:submit 不渲染入口。
  实时消息=本地状态,历史=服务端字段(spec)。
- /admin/feedback:AdminLayout 子导航注册(perm=system:feedback:view,
  与路由 PermRoute 同源;R1-O10① 修正工件「nav.ts 注册」失实表述);
  页=统计卡(5 卡)+趋势折线(echarts,token 色板)+
  筛选区(评分/租户/数据源/处置状态/仅纠错)+明细分页表(处置状态
  Badge 分态)+详情弹窗(对照区+涉及表区+处置区按 handle 权限显隐)。
  「编辑知识」跳转 /admin/rag?table=<表名>(R1-S2 兑现 spec「携带参数」
  scenario——knowledgeId 经定位行高亮呈现,RagPage 读取参数显示定位
  提示条+行高亮,既有页零服务端变化;知识编辑仍经上传链路,
  proposal Non-goal)。

## Risks / Trade-offs

- [纠错沉淀防火墙与执行面规则漂移] → 复用 sqlfw 公共函数零拷贝;
  校验失败 400 与问答 REJECTED 分态文案区分(提交面是用户主动行为,
  返回校验原因友好文案)。
- [行锁竞争下反馈提交延迟] → 单行 FOR UPDATE,冲突窗口毫秒级;
  spec 立约串行语义优先于延迟。
- [reindex 重构改变审计时点] → log_oper 仍在删写事务内(与业务同
  事务语义不变);润色期失败无审计(未发生写操作,语义正确)。
- [统计 Asia/Shanghai 硬编码] → spec 立约固定时区,PG 命名时区
  (非 +08:00)兼容未来若引入 DST 地区只需改一处;zh-CN 部署无 DST。
- [赞踩提交与消息竞态(会话被删)] → 提交端点归属 403 同形兜底,
  前端错误条展示不静默。

## Migration Plan

零 schema 迁移。种子新权限点(排序 6/7/8)随 lifespan 幂等播种——
启动即生效;DEVELOPER 推荐绑定 chat:feedback:submit 只增不摘
(存量 DEVELOPER 用户启动后自动获得反馈能力;system:feedback:*
不推荐绑定,SUPER_ADMIN 自动覆盖)。契约重生成产物随代码提交。
回滚=还原代码;新增权限点行残留无害(不绑定即无权限面)。

## Open Questions

(无——spec 三域已立约全部行为边界,搭车两项为既有评审裁定形态。)
