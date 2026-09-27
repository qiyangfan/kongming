# rag spec delta（070-feedback-sql-promotion-gate）

## ADDED Requirements

### Requirement: 反馈纠错样本来源受控召回

历史 SQL few-shot SHALL 区分真实执行成功样本、反馈纠错样本和来源未知的存量样本。执行成功样本保持现有 tenant + datasource 隔离与同问句幂等覆盖语义。反馈纠错样本 SHALL 仅在来源反馈状态为 ADOPTED 时可召回；PENDING、REJECTED、DISMISSED、重新提交待处置的反馈样本以及来源无法证明的 LEGACY 样本 SHALL NOT 进入提示词。

执行成功样本和反馈纠错样本 SHALL 使用互不冲突的身份键。同问句存在启用反馈样本时，历史路 SHALL 优先返回最近一次采纳的纠错 SQL；否则 SHALL 返回最近一次执行成功 SQL。一次召回对同一原始问句最多返回一条 SQL。

反馈样本状态变化提交后，RAG SHALL 精确失效目标 tenant + datasource 的检索上下文缓存；缓存不可用时遵循既有 fail-open 与 TTL 边界。

#### Scenario: 反馈未采纳时不召回

- GIVEN correctedSql 对应反馈状态为 PENDING、REJECTED 或 DISMISSED
- WHEN 历史 SQL 路召回
- THEN correctedSql 不进入 few-shot

#### Scenario: 同问句反馈样本优先且去重

- GIVEN 同一 tenant、datasource、问句存在执行成功 SQL 和至少一条启用的 ADOPTED 反馈样本
- WHEN 历史 SQL 路召回
- THEN 只返回最近一次采纳的反馈 SQL
- AND 不再返回该问句的执行成功 SQL

#### Scenario: 无启用反馈时回退执行样本

- GIVEN 同一问句的反馈样本均已停用，且存在执行成功样本
- WHEN 历史 SQL 路召回
- THEN 返回执行成功 SQL

#### Scenario: 无可证明来源的旧行隔离

- GIVEN 存量 query_history 行无法精确匹配到成功 assistant 执行或当前 ADOPTED 反馈
- WHEN 完成来源迁移
- THEN 该行标记为 LEGACY、退出召回且保留原始 SQL 数据

#### Scenario: 反馈状态变更后缓存失效

- GIVEN 目标租户+数据源的问句检索上下文已缓存
- WHEN 反馈样本被启用或停用并提交
- THEN 精确失效该租户+数据源的检索缓存，其他租户和数据源缓存不受影响
