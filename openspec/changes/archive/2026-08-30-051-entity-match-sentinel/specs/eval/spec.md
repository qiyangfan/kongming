# eval spec delta(051-entity-match-sentinel)

## ADDED Requirements

### Requirement: 负例评估(如实弃答)

评估集 SHALL 支持「如实弃答」负例形态:`expect_no_data: true` 的用例断言
链路响应为哨兵短路业务终态——success=false、verdict=NO_DATA、不含 SQL
(零执行硬断言),SHALL NOT 与 `expect_reject` 的 HTTP 拒绝形态(fail-closed
400)混用。负例用例的通过由断言保证,SHALL NOT 计入严格 Execution Match
(防虚增,与拒绝用例同口径)。负例集 SHALL 覆盖问题实体与可用表领域错配
的哨兵守护(051 表级判据的回归闸),至少一题进入 smoke 集。

#### Scenario: expect_no_data 用例断言口径

- GIVEN 评估用例标记 expect_no_data(问题实体与可用表领域错配)
- WHEN 链路对该问句生成
- THEN 断言 success=false 且 verdict=NO_DATA 且 SQL 为空
- AND 通过计入宽松 pass、不计严格分(与拒绝用例同口径)

#### Scenario: 实体错配硬凑回归被 smoke 闸拦截

- GIVEN 模型对实体错配问句硬凑生成(哨兵未触发)
- WHEN smoke 集运行含 expect_no_data 负例
- THEN 断言失败并透出实际生成 SQL,阻止纪律劣化入库
