# Proposal: add-doc-event-entity-recall

## Why

文档问答的主要质量缺口形态是多跳:证据分散在多个文档/段落时(如 A 文档定义
概念、B 文档使用概念),向量+全文按单块相似度召回容易漏跳。SAG 对照登记
(roadmap 2026-11-07)确立的借鉴方案是「事件-实体索引 + 查询时动态超边」的
pgvector 单库化:实体做轻量词法索引,共享实体在查询时 join 出相关块邻域,
不预建全局图、零新增中间件——与 ADR-012「单库 SQL 形态终局」同哲学。同日
第一变更(add-retrieval-quality-eval)已交付检索质量基线面,本变更以
before/after 数据验证增益,不盲上。

## What Changes

- rag schema 新增**文档实体伴表**(迁移,ADR-004):实体按父块 LLM 批量抽取、
  落库到子块粒度(实体×子块),作为词法 join 索引;文档删除级联清理。
- 摄入新增**实体抽取端口**(Protocol+组合根注入):部署级开关**缺省关**;
  抽取失败降级为该文档无实体(与能力未开启不可区分),降级留痕可观测;
  上传幂等语义不变。
- 检索新增**实体扩展路**:问句实体识别(一次 LLM 调用)+词法兜底→共享实体
  join 子块→并入融合候选池(并入配额部署级可配);精排、父块回填、稳定
  引用键派生不变;试跑端点实体路逐路可见。
- 管理端 **enrich 端点**:既有文档覆盖式补抽取(权限复用文档管理权限点),
  免重传文件。
- chunker **子块句界校准**(搭车件,登记于 roadmap 2026-11-07):独立部署
  开关缺省关,开启时子块滑窗边界优先对齐句界(保持纯函数);与实体开关
  正交,保证基线归因隔离。
- evals retrieval 层新增对照口径:实体 off/on 两组分数进 README 基线表
  (标注开关态)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `document-rag`: 新增「文档实体索引与抽取」「实体扩展召回与降级」「子块
  句界完整性校准」三个 requirement(均为可选档、缺省关、主检索逐字零回归)。

## Impact

- `apps/api/kongming/rag/`: 新迁移(实体伴表)、ports.py 新端口、
  documents_service/documents_repo(抽取落库+检索扩展)、chunker(句界校准)、
  router(enrich 端点)、config.py(开关与配额)。
- `apps/api/alembic/versions/`: 新迁移一张表。
- `apps/api/evals/`: retrieval 层对照口径 + README 基线表。
- `apps/api/tests/`: 抽取/扩展路/句界校准测试。
- `contracts/openapi.json` + web 类型: enrich 端点契约再生(ADR-005)。
- agent 进程零改动(抽取与扩展全在 api 面,ADR-002 信任边界不动)。

## Non-goals

- 不建全局知识图谱、不做三元组分解、不预建实体间关系(超边=查询时动态
  实例化,SAG 同口径)。
- 不做实体向量索引(实体=轻量词法索引,向量留存块级,pgvector 维度不动)。
- 不做事件摘要列/事件向量(父块回填本身已是语义完整单元,句界校准承担
  chunk 完整性收益)。
- 不动 nl2sql 三路召回与 semantic 域关系扩展(双通道隔离契约不变)。
- 不做后台异步抽取(上传同步+enrich 逃生,异步化另立变更)。
- 无 web 新增面(实体可见性走既有文档详情端点数据,不加界面)。
- 不做检索评测集自动构造。
