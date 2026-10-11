# Spec Delta

## MODIFIED Requirements

### Requirement: 文档检索(small-to-big + 混合召回)

文档检索 SHALL:子块向量 top-K + 子块全文 top-K,RRF 融合(复用既有融合函数),按 parent_id 集合回填父块全文(父块数有界,缺省 top-5);与 schema 通道(table_descriptions)SHALL 物理+逻辑双隔离(文档命中不进 schema_hint,表说明不进文档段)。**文档面的全文分词配置 SHALL 与表说明全文路共用同一部署配置(单一配置单源):写入与检索写读同配置,SHALL NOT 出现同库两套分词语义;配置不可用时的 fail-closed 语义、白名单约束与配置变更后的缓存边界与 rag 全文路同口径。**

#### Scenario: small-to-big 回填

- GIVEN 问句命中某子块
- WHEN 检索执行
- THEN 返回其父块全文(非孤立子块片段),并带 source_ref

#### Scenario: 文档全文与表说明全文同配置

- GIVEN 全文检索配置为中文分词配置
- WHEN 上传含中文表说明的文档并以其内容关键词检索
- THEN 文档全文向量按同一分词配置写入与检索,与表说明全文路行为一致

#### Scenario: 文档面无独立配置入口

- GIVEN 管理员检查部署配置
- WHEN 查找文档检索的分词配置
- THEN 不存在独立的第二配置项(与 rag 全文路同一单源)
