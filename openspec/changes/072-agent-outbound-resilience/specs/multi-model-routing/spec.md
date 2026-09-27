# multi-model-routing spec delta(072-agent-outbound-resilience)

## MODIFIED Requirements

### Requirement: 任务级路由策略

LLM 调用 SHALL 按任务类型路由:任务类型枚举(SQL_GEN/UNDERSTAND/SUMMARIZE/TITLER/DESCRIBE/REACT)经路由表(环境变量)映射模型名;未配置路由的任务 SHALL 回退主模型(既有 llm_model)。路由 SHALL 确定性(同配置同结果,可审计);既有调用方(不传任务类型)SHALL 走主模型零回归。embedding 调用 SHALL NOT 参与路由(独立配置语义不变,009 立约)。

运行期降级与重试 SHALL NOT 篡改路由表、注册表或任何持久状态:同配置的后续请求 SHALL 仍按同一路由表解析目标;降级只作用于当次调用的尝试序列,且当次实际使用的模型 SHALL 在用量记录中可判别。

#### Scenario: SQL 生成走指定模型

- GIVEN 路由表配置 SQL_GEN → deepseek-coder
- WHEN 问答链路执行 generate 节点
- THEN 该次 LLM 调用发往 deepseek-coder 的 base_url 与模型名

#### Scenario: 未配置任务回退主模型

- GIVEN 路由表只配置了 SQL_GEN
- WHEN summarize 节点执行
- THEN 该次调用走主模型

#### Scenario: 既有调用零改动

- GIVEN 未传任务类型的调用(如标题改写既有代码)
- WHEN 执行
- THEN 走主模型,行为与 021 之前一致

#### Scenario: 降级不改写路由状态

- GIVEN SQL_GEN 路由到 deepseek-coder,且该模型对某次调用连续瞬时失败
- WHEN 该次调用降级至主模型完成
- THEN 后续 SQL_GEN 调用仍解析到 deepseek-coder
- AND 降级仅体现在该次用量的模型字段中

## ADDED Requirements

### Requirement: 出站调用韧性

agent 面出站调用(LLM 供应商调用与控制面回调)SHALL 对瞬时类供应商故障自动恢复,并对非瞬时类故障立即失败。规则如下:

1. **瞬时故障重试**:连接建立失败、读超时、HTTP 408/429/5xx 及协议层等价错误 SHALL 触发同一调用点重发。重试次数、退避基线、退避上限 SHALL 可配置,缺省开启。退避 SHALL 指数增长并叠加随机抖动;供应商 `Retry-After` SHALL 被尊重但受退避上限截断,以防长等待穿透上层请求时限。
2. **非瞬时故障不重试**:HTTP 4xx(408/429 除外)与配置类错误(缺凭证、模型不存在、协议非法)SHALL NOT 重试——重试只放大成本并掩盖配置错误。
3. **尝试预算**:单次业务调用的尝试总数 SHALL 有硬上限(可配,缺省 3);最后一次尝试 SHALL 可为降级目标,且降级目标本身 SHALL NOT 再触发重试,使最坏耗时受单轮问答总体时限约束。
4. **单层降级链**:任务路由目标瞬时故障耗尽 SHALL 降级至主模型;主模型也耗尽且显式配置了降级条目 SHALL 降级至该条目;否则失败。Bot 模型覆盖(`model_override`)SHALL NOT 参与降级——显式选择的模型与计价不容被静默替换。
5. **用量每尝试一条**:每次尝试 SHALL 各落一条用量记录(实际模型名、成败、耗时),SHALL NOT 合并为单条。失败尝试计入按次配额是既有口径的延续,非本需求引入的偏差。
6. **回调分层**:控制面回调 SHALL 按可重试性分层——元数据/召回/附件类只读回调与「请求未送达」类失败 SHALL 可重试;用量上报 SHALL NOT 重试(重复落库);工具执行 SHALL NOT 重试(工具副作用不可假定幂等)。

#### Scenario: 供应商限流后按 Retry-After 恢复

- GIVEN 供应商对首次调用返回 429 且带 `Retry-After`
- WHEN agent 重试
- THEN 按 `Retry-After`(受退避上限截断)等待后重发
- AND 调用成功时用户侧无失败感知,用量记录最终成功态

#### Scenario: 鉴权失败不重试

- GIVEN 供应商返回 401(凭证错误)
- WHEN 该次 LLM 调用执行
- THEN 只发生一次出站调用,直接失败并按既有 ERROR 口径落用量

#### Scenario: 路由目标故障降级主模型

- GIVEN SQL_GEN 路由到 deepseek-coder,该供应商连续瞬时失败
- WHEN 重试预算耗尽
- THEN 该次调用由主模型完成,用量记录的实际模型为主模型
- AND 后续 SQL_GEN 调用仍解析到 deepseek-coder

#### Scenario: Bot 显式模型不降级

- GIVEN 某 Bot 配置 `model_override` 指向注册表某条目
- WHEN 该条目连续瞬时失败
- THEN 不降级到主模型,该轮以失败收口

#### Scenario: 执行回调读超时不重试

- GIVEN agent 调用控制面执行 SQL,请求已送达但读超时
- WHEN 判定重试资格
- THEN 不重发(执行可能已发生,重复执行不可接受)

#### Scenario: 用量上报失败不重试

- GIVEN 用量上报请求失败
- WHEN 上报逻辑执行
- THEN 不重发,仅按既有 fire-and-forget 口径记录日志

#### Scenario: 尝试预算硬约束

- GIVEN 任何失败序列(连续瞬时故障 + 触发降级)
- WHEN 单次业务调用执行
- THEN 出站尝试总数不超过配置上限,最坏耗时不超过单轮问答总体时限
