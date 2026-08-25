## ADDED Requirements

### Requirement: 文档通道与知识分流节点

retrieve 节点 SHALL 在三路召回之外并行取文档通道检索结果(doc_hints:父块全文+source_ref 列表;零命中为空);分流判定(规则词表+schema 空+文档非空)成立时进入 doc_answer 节点直接产出文本答案(summary=回答,无 SQL),否则走既有链路。doc_answer 的提示 SHALL 只含问句与文档段(全新撰写资产),答案 SHALL 以文档内容为准并标注 source_ref;文档段超限时按序截断。既有节点链路与 prompt 资产 SHALL NOT 变化;文档通道失败 SHALL 降级为空(既不影响 schema 通道)。

#### Scenario: 分流节点零 SQL 面

- GIVEN 分流条件成立
- WHEN doc_answer 执行
- THEN 终态无 generated_sql、无执行回调,summary 为文档答案

#### Scenario: 文档通道失败降级

- GIVEN 文档检索服务异常
- WHEN retrieve 执行
- THEN doc_hints 为空,链路按无文档处理(与 024 之前一致)
