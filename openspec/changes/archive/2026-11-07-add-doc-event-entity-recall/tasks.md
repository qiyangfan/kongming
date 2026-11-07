# Tasks: add-doc-event-entity-recall

## 1. 迁移与领域模型

- [x] 1.1 新迁移:rag.document_chunk_entities(id UUID v7 PK、tenant_id、datasource_key、document_id FK CASCADE、chunk_id FK CASCADE 子块、name、name_normalized、created_at timestamptz;索引 (datasource_key,name_normalized) 与 (chunk_id))(ADR-004)(验证:alembic upgrade head + downgrade 往返)
- [x] 1.2 models 实体名规范化纯函数(NFKC+小写+空白折叠)与 repository 落库/清理(验证:单测覆盖规范化边界)

## 2. 实体抽取(测试先行)

- [x] 2.1 先写失败测试:开启抽取落库(父块批量→子块行)、失败降级零中断且可观测、未启用零存在感、enrich 覆盖式(先清后抽)、文档/数据源删除级联清理、enrich 非超管 403(验证:pytest 该文件红)
- [x] 2.2 ports.py 新增实体抽取端口(Protocol)+组合根注册:部署级开关(缺省关)+LLM 连接注册+出站统一通道语义(白名单/超时/错误分类)(验证:开关关闭时无端口装配痕迹)
- [x] 2.3 上传编排插入抽取步(父块批量调用→子块实体落库),失败降级留痕(sys_llm_usage 同口径)(验证:2.1 对应项转绿)
- [x] 2.4 实现 POST /system/document/documents/{id}/enrich 覆盖式补抽取(验证:2.1 对应项转绿)

## 3. 检索实体扩展路(测试先行:租户过滤)

- [x] 3.1 先写失败测试:多跳补跳(两文档证据均入候选池)、未启用逐字零回归、识别不可用降级词法兜底、租户+数据源双过滤(越界不进池)、候选配额截断、试跑端点实体路逐路可见(验证:pytest 该文件红)
- [x] 3.2 retrieve_docs 增实体扩展步:问句实体识别+词法兜底→共享实体 join→按共享数截断配额→并入融合池;精排/父块回填/引用键派生不变(验证:3.1 对应项转绿)
- [x] 3.3 试跑端点响应增实体路命中(未启用时无该路痕迹)(验证:3.1 对应项转绿)

## 4. chunker 句界校准(搭车)

- [x] 4.1 先写失败测试:开启后子块边界落句界(单句超长才句中截断)、关闭与既有切分逐字一致(验证:pytest 该文件红)
- [x] 4.2 实现(独立部署开关缺省关,纯函数)(验证:4.1 转绿;既有 chunker 测试全绿)

## 5. evals 对照口径

- [x] 5.1 retrieval 层 runner 支持实体开关两组运行(off/on),输出逐组聚合分(验证:真栈两组各跑一轮)
- [x] 5.2 README 基线表记录 off/on 对照与①基线(标注开关态与题量)(验证:基线行存在)

## 6. 契约与门禁

- [x] 6.1 make contracts 再生契约产物(enrich 端点)(验证:contracts/openapi.json 与 api-types.gen.ts 更新)
- [x] 6.2 全套 make check(验证:api/agent/web 三侧绿;contracts 未提交树机制性红按既知口径记录)
