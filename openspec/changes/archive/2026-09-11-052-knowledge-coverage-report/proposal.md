# 052-knowledge-coverage-report 提案:知识覆盖报告(空白探测)

## Why

NL2SQL 生成质量的上限由表结构知识的覆盖密度决定,而知识缺口目前只能靠人巡发现
(sakila_ext 165 列零注释是人工排查的成果;零覆盖表/无标签列/示例缺口均无系统性
盘点手段)。LLM Wiki(Karpathy 2026)的 gap detection 方法论值得移植——但其
「LLM 编译 Wiki」实现已被否决(非确定性生成物进入知识面与 041/051 毒知识教训
同源)。本变更取其「空白探测」思想、弃其生成式实现:做一个**纯确定性、零 LLM、
零写入**的知识覆盖报告,把「哪里缺知识」从人巡变成机器盘点,直接喂给 041 标签
补全与示例 SQL 治理的人工/治理流程。

## What Changes

- 新增管理面只读端点 `GET /system/rag/coverage-report?datasource_key=…`
  (system:rag:manage 权限;数据源校验与 fetch-metadata 同形:存在+启用+归属
  当前租户,否则同形拒绝),按 (租户, 数据源) 一次性编译知识覆盖报告,覆盖
  四类空白:
  1. **零覆盖表**:内省扫描集(与 fetch-metadata 同语义:default_schema 的
     BASE TABLE,排除系统 schema 与保留表)中存在真实结构但无知识登记的表;
  2. **纯结构登记**:已登记但来源为 INTROSPECTION(润色降级,零语义内容)的表;
  3. **缺标签列**:已登记表的内省列名集合 − 知识条目 columns_comment 键集合,
     按表聚合缺失列清单与计数;
  4. **失败问题簇**:该数据源近期会话消息中 FAILED/REJECTED/NO_DATA、差评
     (rating=DOWN)或已有 corrected_sql 的问题,按归一化问题文本去重计数,
     附最近状态与时间。
- 报告同时给出 few-shot 通道概况:该 (租户, 数据源) 的 query_history 成功
  沉淀条数、去重问题数与上限占用率,以及 sample_queries 为空的登记表清单。
- **纯读聚合契约**:零 LLM 调用、零数据库写入、无缓存;内省失败
  (连接/凭证异常)SHALL NOT 产出半份报告,整体按失败返回(宁缺毋错,
  与 042 对账的内省失败口径一致)。
- 契约管线:新端点纳入 OpenAPI 契约,`make contracts` 重新生成并提交产物
  (ADR-005);web/agent 类型由生成获得,禁手写。

## Non-goals

- **不做知识编译层**(LLM Wiki 式「Wiki 即检索层」替代方案已否决):非确定性
  生成的知识进入检索面会自我繁殖(041 回写繁殖、051 硬凑沉淀实证);运行时
  检索/生成链路一行不动。
- **不做任何持久化与沉淀**:报告零写入,即时计算即时返回(051 教训:沉淀类
  产物须先试运行;本工具不产生沉淀物,天然无毒)。
- **不做语义聚类**:失败问题簇按归一化文本精确去重;embedding 聚类/主题归纳
  属后续独立演进。
- **不做自动修复**:不自动补标签、不自动生成示例 SQL——自动生成知识正是被
  否决路径;报告只盘点,修复走既有治理路径(管理面手工编辑/上传,follow
  覆盖优先级)。
- **不做前端呈现页面**:JSON 端点先服务治理者(curl/jq 可用);管理面报告页
  属独立 change。
- 不引入定时任务/后台编译(按需触发;调度属独立演进)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `knowledge`:新增「知识覆盖报告」Requirement——四类空白的盘点口径、只读
  纯聚合契约、权限与数据源校验同形、内省失败不产出半份报告。

## Impact

- `apps/api/kongming/rag`:router(端点适配)、service(报告编排)、
  repository(内省复用 `introspect_all` + chat_message 聚合查询)、models
  (覆盖差集纯函数);跨域只读 `public.chat_message`(feedback 域已有同表
  只读先例),不写任何表。
- `contracts/openapi.json`:新端点契约,重新生成入库(CI 漂移门禁,ADR-005)。
- 无 schema 变更(不动 baseline,ADR-004 无涉);apps/agent 零改动(信任
  边界 ADR-002 无涉,不新增 api↔agent 回调);apps/web 零代码改动(仅类型
  再生成)。
