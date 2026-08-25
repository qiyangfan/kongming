## ADDED Requirements

### Requirement: 用量采集计费口径

sys_llm_usage(服务端 fire-and-forget 采集,不经客户端)SHALL 作为
配额与计费的用量事实源;「统计口径非计费依据」的域内旧注按
developer-portal-billing 域转正——v1 计费以该表聚合为准,采集丢失
场景下的宽松语义(偏低估)在文档明示。

#### Scenario: 计费以服务端采集为准

- GIVEN 配额判定或账单生成
- WHEN 聚合用量
- THEN 仅取 sys_llm_usage(无客户端上报通道)
