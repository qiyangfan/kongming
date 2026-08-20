# 009 任务清单 — RAG 三路召回 + 知识库

## 1. 数据与治理底座(测试先行)

- [x] 1.1 迁移 0006(ALTER 型,手写——autogenerate 不识别 vector/hnsw):
   table_descriptions source_type CHECK 枚举替换(MARKDOWN→INTROSPECTION/
   AI_GENERATED/MANUAL_UPLOAD/MANUAL_EDIT/FEEDBACK_REFINED,DEFAULT 改
   INTROSPECTION,空表瞬时)+ 辅助索引 idx_td_tenant_ds/idx_qh_tenant_ds_
   status;downgrade 还原自身增量(不触碰 baseline 资产);真实 PG 跑通升降级
- [x] 1.2 种子权限 system:rag:manage(排序 5,SUPER_ADMIN 覆盖、DEVELOPER
   不推)+ 种子断言测试更新
- [x] 1.3 rag 域包骨架(models/ports/service/repository/parser/router)+
   import-linter:auth 防线猜测名 kongming.rag_admin→kongming.rag,新增
   「rag models/service/ports 零框架」契约;lint-imports 绿
- [x] 1.4 sqlfw 拒读扩容(评审 B2,先于任何知识灌入):保留表判定加 rag
   schema 限定名(db=='rag' 即拒;末段名不进清单防误伤业务同名表);
   测试先行(显式限定名/CTE 体内限定/裸名/业务表不误伤)

## 2. 知识写入链路

- [x] 2.1 parser 纯函数:`## 表名` 切块正则+小节提取+columns/示例解析+表名/
   schema 小写折叠(D8 规则),零表块 400;测试先行(含全角逗号/_raw 兜底/
   schema.table 形态/Customer=customer 折叠)
- [x] 2.2 agent `POST /internal/embedding`(body {texts:[…]} 批量,上限 64):
   llm.py 加 embed 方法;settings 独立 embedding_base_url/api_key/model
   (缺省回退 llm_*,上游 028 教训);端点+LLM mock 测试
- [x] 2.3 api EmbeddingPort(httpx 回调+km:emb:v1 Redis 缓存 24h fail-open);
   维度断言(1536)
- [x] 2.4 upsert 链路:embedding 文本公式单一实现点(表名+表说明+字段说明),
   ON CONFLICT(四元)DO UPDATE(含 tsv 应用层重建 to_tsvector('simple',…));
   幂等回归测试;向量一律绑定参数 CAST(:v AS vector)纪律
- [x] 2.5 上传端点 POST /system/rag/documents/upload(multipart;md/markdown/
   txt ≤1MB;manage 权限+租户上下文+datasource 同形校验+schema_name
   PG 标识符校验+表块数上限 500+审计);权限/越权/超限/格式/块数超限/
   非法 schema_name 测试先行
- [x] 2.6 PDF/Word 解析:pypdf+python-docx 依赖,扩展名分流,asyncio.
   to_thread 执行,解析失败/空文本 400,pdf/docx ≤10MB;解析测试(构造
   最小 pdf/docx fixture)

## 3. 三路召回与内部端点

- [x] 3.1 RRF 融合纯函数(k=60,top5)+ schema 提示构造纯函数(表/列/示例
   拼形);测试先行(含 bed_bath_table 精确命中修正排序场景)
- [x] 3.2 三路召回 SQL(rag. 限定+tenant/ds 双过滤 WHERE+绑定参数;向量
   <=>/全文 plainto_tsquery+ts_rank/历史 status=SUCCESS)+ embedding 失败
   降级纯全文路;双过滤隔离测试先行(跨租户同名表/跨数据源不串)
- [x] 3.3 POST /internal/rag/retrieve:租户解析+datasource 校验与 meta.py
   同构,ctx 缓存(km:ragctx 10min fail-open),回写缓存;缓存命中/穿透/
   fail-open 测试
- [x] 3.4 缓存精确失效:上传/删除/fetch-metadata/reindex commit 后
   scan_iter 失效(仅目标 tenant+ds)+ datasource 删除挂 ctx 失效;失效
   范围测试(他租户缓存不受扰)

## 4. knowledge 域(抓取/润色/重建)

- [x] 4.1 agent describer.py:润色 prompt(上游逐字移植,文件头标注来源)+
   输出防御清洗(真实列过滤/SELECT-only/无 INTO/无中间分号/≤500/≤3 条/
   截断 200/全空降级)+ POST /internal/describe-table 端点;清洗与降级
   测试先行
- [x] 4.2 fetch-metadata 端点:全量内省(与 meta.py 同口径:default=
   [tenant_x, public]/动态源=[default_schema],BASE TABLE+VIEW,保留表
   排除,无 50 表截断)+ 逐表润色(并发 4,单表 60s)+ 覆盖优先级(D5
   应用层判定,AI 不覆盖 MANUAL_*)upsert + ctx 失效 + 审计
   rag.fetch_metadata;返回 {found, enriched, skipped};优先级/降级/
   无权限/数据源校验测试先行
- [x] 4.3 reindex 端点:清空+重建+审计+幂等(连续两次一致)测试

## 5. 问答链路切换与回写

- [x] 5.1 apiclient.rag_retrieve(用 resolved_question 召回)+ AgentState.
   history_sql;retrieve 节点切换:召回优先、零命中/失败回退 fetch_schema
   内省(D4);generate user 模板追加「参考历史成功 SQL」段(系统模板零
   改动);节点测试先行(召回命中/空回退/失败回退/历史段拼形)
- [x] 5.2 chat 回写:最终 SUCCESS 判定后(不依赖行数)writeback(question=
   原始问句,emb 缓存复用,独立 try 不阻断,persist=False 不触发);
   成功回写(含零行)/失败不回写/回写异常不阻断测试
- [x] 5.3 端到端图测试更新:召回链路贯通(mock recall),旧内省路径回归

## 6. 管理面与契约

- [x] 6.1 知识列表 GET /system/rag/tables(manage 权限+租户,分页+
   datasource 筛选,updated_at 倒序,不取向量列)+ 删除 DELETE
   /system/rag/tables/{id}(manage+硬删+失效+审计+越租户同形 404);
   测试先行
- [x] 6.2 make contracts 重生成 openapi.json+前端类型;web tsc 绿
- [x] 6.3 web /admin/rag 第五子页:列表/筛选/分页+上传(文件选择+数据源/
   schema)+删除受控确认+抓取元数据+重建索引受控确认(结果展示);admin
   子导航加「知识库」;组件全走 shadcn(样式铁律)
- [x] 6.4 web 页面冒烟自查:无 manage 权限登录者无入口+直达 403

## 7. 质量门禁与冒烟

- [x] 7.1 make check 全绿(api ruff/mypy strict/lint-imports/pytest + agent
   同门禁 + web tsc/eslint);预计 api +80 上下、agent +15 上下
- [x] 7.2 真环境冒烟(Playwright MCP+真 LLM):装载知识(fetch-metadata
   sakila 真 LLM 润色→查库验 AI_GENERATED/向量落库)→召回问答(「有多少部
   电影」命中知识库;日志实证提示收窄非内省)→历史回写闭环(首问成功→
   相似问句二问召回 few-shot)→上传 md 幂等→reindex 结果一致→rag 拒读
   (`SELECT * FROM rag.query_history` 经问答链路 BLOCKED)→越权面
   (smoke-self 无 manage 403)→UI 链(上传/删除/重建确认)
- [x] 7.3 独立评审循环(实施后全量变更面复审),发现项清零;HANDOFF 更新
