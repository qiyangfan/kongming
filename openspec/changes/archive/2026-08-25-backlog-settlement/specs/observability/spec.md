# observability 规格增量:明细分页租户筛选

## MODIFIED Requirements

### Requirement: 用量明细分页查询

系统 SHALL 提供用量明细分页端点(按 created_at 倒序),支持模型、任务类型、
成败状态、trace_id 精确、租户五类筛选(租户按 tenant_id 直滤——该租户全部
行均返回,含 tenant_key 反解失败展示为 unknown 的行);每行返回时间、模型、
任务、status、tokens、latency_ms、trace_id、租户(可空,反解失败归 unknown)。

#### Scenario: 筛选与分页

- GIVEN 库内多条用量记录
- WHEN 按 model+status 筛选并翻页
- THEN 仅返回匹配行,倒序分页,total 与页内容一致

#### Scenario: 按 trace_id 定位

- GIVEN 一次问答产生的一组用量记录
- WHEN 以该 trace_id 精确筛选
- THEN 仅返回该次请求的全部 LLM 调用记录

#### Scenario: 按租户筛选

- GIVEN 租户 T1 与 T2 各有用量记录,且 T1 存在一行 tenant_key 反解失败(展示为 unknown)
- WHEN 以 T1 的 tenant_id 筛选
- THEN 返回 T1 的全部记录(含该 unknown 展示行),T2 记录不返回
