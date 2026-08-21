# rag 能力域 delta(016)

## MODIFIED Requirements

### Requirement: 成功 SQL 回写 query_history

查询链路最终判定成功(status=SUCCESS)的查询 SHALL 回写 rag.query_history
(tenant/datasource/question/sql_text/embedding)——时机为查询链路完成最终
判定后(chat 域消息落库路径);question SHALL 取用户原始问句(与审计口径
一致);成功判定不依赖结果行数(空结果的成功查询同样回写)。失败/拦截查询
SHALL NOT 回写,避免错误 SQL 被召回;回写失败不阻断主流程(独立事务边界,
仅告警)。

回写 SHALL 幂等治理(消重复沉淀):(tenant, datasource_key, question) 同键
重复回写 SHALL 覆盖该行(保留最新成功 SQL,不新增行)——同题多行会重复占据
few-shot 召回位;embedding 计算失败时 SHALL 保留该行旧向量(旧向量语义仍
有效,清空会使该行退出向量召回)。每 (tenant, datasource_key) 维度 SHALL
维持总量上限(超出时淘汰 created_at 最旧行)。

#### Scenario: 成功查询自动回写

- **GIVEN** 用户提问成功(status=SUCCESS,含零行结果的成功查询)
- **WHEN** 查询链路完成最终判定
- **THEN** 同步回写 query_history(tenant, datasource_key, question=原始问句, sql_text, status=SUCCESS, embedding 复用问句向量缓存,不二次调用 embedding)
- **AND** 回写失败不阻断主流程(仅告警日志)

#### Scenario: 失败/拦截查询不回写

- **GIVEN** 查询失败或被拦截
- **WHEN** 查询链路完成最终判定
- **THEN** 不回写 query_history(只记成功 SQL,避免错误 SQL 被召回污染 few-shot)

#### Scenario: 同题重复回写幂等覆盖

- **GIVEN** 同一 (tenant, datasource_key, question) 已有成功回写行
- **WHEN** 该题再次成功回写(如反馈纠错后重查、原 SQL 再次成功)
- **THEN** 覆盖既有行(sql_text 更新为最新成功 SQL),行数不增长
- **AND** embedding 计算失败时保留旧向量,不清空

#### Scenario: 总量上限淘汰

- **GIVEN** 某 (tenant, datasource_key) 维度回写行数达到上限
- **WHEN** 新回写写入
- **THEN** 淘汰该维度 created_at 最旧行,总量不超上限
