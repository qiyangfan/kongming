## ADDED Requirements

### Requirement: 块索引上下文

系统 SHALL 以「文档名 + 块正文」构造子块的被索引文本,用于 embedding 与全文 tsv;
返回给模型与引用的块正文 SHALL 保持原文,不含文档名前缀。存量块 SHALL 可通过重建
脚本按当前分词配置重算 embedding 与 tsv,重建不重新切块、不改变块编号与父块关系。

#### Scenario: 文档名可被召回

- **GIVEN** 文档正文不含文档名,问题以文档名或文档级实体提问
- **WHEN** 检索该数据源
- **THEN** 命中该文档的相关父块(判别语料融合 Recall@5 较改造前提升 ≥5pp)

#### Scenario: 重建不改变对外可见内容

- **GIVEN** 存量文档已按旧索引入库
- **WHEN** 执行重建脚本
- **THEN** 块正文、引用键与检索试跑结构逐字不变,embedding 与 tsv 按上下文文本重算

#### Scenario: 既有语料不劣化

- **GIVEN** 既有 15 文档与实验室定版语料题集
- **WHEN** 改造前后各跑一轮检索评测
- **THEN** macro Recall@5 劣化不超过 5 个百分点(超过即失败)
