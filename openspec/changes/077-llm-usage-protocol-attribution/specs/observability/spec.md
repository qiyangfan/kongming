# observability spec delta(077-llm-usage-protocol-attribution)

## ADDED Requirements

### Requirement: 用量协议归因

LLM 用量记录 SHALL 携带**出站协议**(`protocol`)字段,取值域
`openai | anthropic | responses`(与多模型注册表同源,ADR-008),SHALL NOT
引入第四个取值或第二套枚举。存量用量行(迁移前写入)该字段 SHALL 为 null,
SHALL NOT 从 model 名反算或推断;null 在展示层的口径 SHALL 与「-」一致。

用量明细分页 SHALL 支持按 protocol 过滤,SHALL 在每行返回 protocol(可空);
用量汇总 SHALL 支持按 protocol 过滤(过滤型,SHALL NOT 因此新增分组维度)。
协议维度的筛选与返回 SHALL NOT 改变既有权限模型(`system:llm-usage:view`)
与租户收敛口径。

#### Scenario: 落库携带协议

- GIVEN 一次 LLM 调用经 `anthropic` 通道执行(成功或失败)
- WHEN 该次用量记录落库
- THEN 记录含 `protocol=anthropic`;经 `openai` 通道者落 `openai`,以此类推

#### Scenario: 同名模型跨协议拆分

- GIVEN 注册表两条目 model 同名、protocol 分别为 openai 与 responses,
  且均已产生用量
- WHEN 以 protocol=responses 筛选明细分页
- THEN 仅返回 responses 通道的用量行;以 openai 筛选时反之,两股流量可拆分

#### Scenario: 存量行 null 口径

- GIVEN 迁移前写入的历史用量行
- WHEN 明细分页返回该行
- THEN protocol 为 null,展示层呈现「-」,且 SHALL NOT 被后端推断改写

#### Scenario: 协议级故障可见

- GIVEN 某协议通道(如 anthropic 翻译层)连续多轮 ERROR
- WHEN 以 status=ERROR 且按该 protocol 筛分明细
- THEN 可定位故障集中于该协议通道,无须翻 agent 进程日志
