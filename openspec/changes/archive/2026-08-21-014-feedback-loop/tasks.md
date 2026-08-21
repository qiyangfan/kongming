# 014 反馈闭环 — 实施任务

## 1. api:feedback 域包骨架 + 种子 + 契约

- [x] 1.1 kongming/feedback/ 域包(models 纯函数:覆盖三态判定/状态枚举;ports:FeedbackRepo Protocol+ChatStoreError 同款转译或复用 chat 转译边界裁定;repository:消息归属查询/角色 ALL 判定/提交行锁覆盖写/处置写/分页/统计/涉及表匹配;service 零框架;router 双前缀 /chat+/feedback)
- [x] 1.2 auth/seed.py:PERMISSIONS 增 (chat:feedback:submit,6)/(system:feedback:view,7)/(system:feedback:handle,8);ROLE_RECOMMENDED_PERMS 增 DEVELOPER:[chat:feedback:submit];种子断言测试更新
- [x] 1.3 pyproject import-linter 契约补 kongming.feedback models/service/ports 零框架;lint-imports 过

## 2. api:反馈提交端点(D1/D2/D3,测试先行)

- [x] 2.1 测试先行:test_feedback_submit——UP/DOWN 落库+PENDING 置位+审计落;403 同形(跨用户/跨租户);user 消息 400;无权限 403;幂等覆盖(null 保持/显式覆盖/空串清空/feedback_time 最新);DELETE correctedSql 400 整体拒(反馈列不变);相同 correctedSql 不重复沉淀
- [x] 2.2 repository.submit 行锁读现值+覆盖写;service 三态判定+防火墙校验(sqlfw.check+check_reserved_tables+check_schema_scope);commit 后 writeback 沉淀(最近 user 问句;失败告警不阻断);审计 feedback.submit 同事务
- [x] 2.3 router:POST /chat/messages/{messageId}/feedback 挂 chat:feedback:submit;传输层 rating 枚举/comment≤500/correctedSql≤4000
- [x] 2.4 测试转绿

## 3. api:messageId 打通(D4)

- [x] 3.1 ChatOutcome/ChatQueryOut 纯增 message_id+datasource_key;service 落库段透传 mid(含 AgentUnavailable 分支);persist=false 恒 null
- [x] 3.2 list_messages SELECT 补 rating/corrected_sql;_to_message 透传(user 恒 None);既有回显测试断言更新
- [x] 3.3 测试:实时响应含 message_id(与库内 assistant 消息一致)/历史消息反馈字段回显

## 4. api:处置/涉及表/统计/分页(D5/D6/D7,测试先行)

- [x] 4.1 测试先行:test_feedback_admin——status 枚举非法 400/处置落库 handler+time+审计/行级可见性(非 ALL 强制本人,覆盖 userId 传值)/involved-tables(JOIN/子查询/CTE 形态抽表+hasKnowledge 匹配+解析失败 parseError)/overview(补零 trend/downRate 分母零)/page(feedbackStatus/onlyCorrected 筛选+倒序)
- [x] 4.2 service+repository:处置写(D2 同范式行锁非必需,单 UPDATE 即可)/涉及表 sqlglot 抽取(CTE 排除,末段小写)/overview AT TIME ZONE 分桶+generate_series 补零/page 多筛选动态 WHERE
- [x] 4.3 router:PUT /feedback/{id}/status(handle)/GET /feedback/{id}/involved-tables+overview+page(view)
- [x] 4.4 测试转绿

## 5. api:rag 搭车(D8 列注释/D9 reindex 执行序)

- [x] 5.1 _introspect_all_on 补 col_description 查询,组装 columns_comment=db_comment or data_type;测试:带注释列进润色输入(既有内省测试断言更新+新用例)
- [x] 5.2 reindex 重构先润色后删写(introspect→describe→embed→短事务 delete+upsert+log+commit);测试:reindex 幂等/{deleted,rebuilt,enriched} 语义/润色期失败不删既有条目(模拟 introspect 异常)
- [x] 5.3 rag 58+测试零回归

## 6. web:赞踩交互(D10a)

- [x] 6.1 lib/feedback-api.ts 封装(类型全生成);assistant-card 操作行赞/踩(无 msg.id/无权限不渲染;已反馈激活态回显)
- [x] 6.2 FeedbackDialog(comment+correctedSql 预填 msg.sql,清空可;提交中禁用;错误行内 role=alert);赞=立即提交
- [x] 6.3 实时消息本地状态+历史服务端字段两路径;ChatQueryOut 新字段消费(message_id 保留进消息对象)

## 7. web:反馈分析页(D10b)

- [x] 7.1 AdminLayout 子导航注册 /admin/feedback(perm=system:feedback:view,与路由 PermRoute 同源;R1-O10① 修正)
- [x] 7.2 FeedbackPage:统计卡 5+趋势折线(token 色板)+筛选区(评分/租户/数据源/处置状态/仅纠错/时间)+明细分页表(处置状态 Badge 分态;删除末页越界自愈范式复用)
- [x] 7.3 详情弹窗:对照区(问句/原 SQL/纠错 SQL/留言)+涉及表区(hasKnowledge「编辑知识」跳 /admin/rag?table=<名>,RagPage 定位提示条+行高亮,R1-S2)+处置区(handle 权限显隐;状态下拉+备注+保存)
- [x] 7.4 tsc+eslint 零告警

## 8. 门禁与冒烟

- [x] 8.1 make check 全绿;make contracts 重生成(5 新端点+chat 响应纯增)
- [x] 8.2 真 LLM 冒烟:①问答响应含 message_id;②UI 点赞立即提交+回显激活;③点踩弹窗预填 SQL,改错 SQL 提交(防火墙拒 400 可见);④改正 SQL 提交成功→query_history 沉淀查库;⑤相似问句再提问召回 few-shot 命中纠错 SQL;⑥/admin/feedback 统计卡/趋势/筛选/分页/详情弹窗/处置保存;⑦涉及表定位+「编辑知识」跳转;⑧无权限用户(smoke-self)赞踩入口不可见+直达 403;⑨reindex 两次幂等+列注释进润色(数据库带注释列的表润色后说明更准);Playwright MCP 留痕
- [x] 8.3 R1 独立评审(作者/评审分离)→修复→复核→归档
