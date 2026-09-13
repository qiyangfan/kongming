# 055-usage-cost-reconciliation 提案:LLM 成本对账管道(牌价与网关 credit 双轨收敛)

## Why

kongming 的 LLM 计费是**双轨并存、零对账**(2026-09-13 聚合复查确认为三仓聚合层真实缺陷):

- **产品轨(自有)**:开发者门户按 token 数 × 自有牌价(`price_per_mtok` 厘/千 token)+ 调用次数计费,`sys_invoice` 整数厘,fail-closed 三维配额(`apps/api/kongming/billing/`、`sys_llm_usage` 用量采集)。
- **成本轨(网关)**:实际 LLM 支出自 2026-09-11 起经 muniu 网关,按 credit(本位币 1e-9)逐上游尝试计量(ADR-007 已切换,灰度中)。

两轨之间**没有任何核对管道**:kongming 代码对 muniu 的引用仅剩一条测试注释,无 ingest、无拉取、无换算、无差异告警。后果是——上游改价、muniu 计量漂移、汇率/币种换算错误、模型路由打错价档,任何一项发生都**不可见**,牌价账单与真实成本静默背离。muniu 仓 HANDOFF「下一步 6b」已登记跨仓裁决(kongming 侧计价归一),但 kongming 仓文档(ADR-007、HANDOFF)对此**只字未提**,属跨仓决策单边记录。

## What Changes

1. **新增成本对账只读端点**:`GET /system/billing/cost-reconciliation`(管理面),拉取 muniu 管理 API `GET /admin/v1/usage` 汇总(key 维度、credit 口径),与本侧 `sys_llm_usage`/`sys_invoice` 同期汇总并列呈现:token 总量、牌价账单额(厘)、网关 credit 成本额、时间窗。**不做厘↔credit 硬换算**(两侧牌价本就是产品定价,允许合理加成),比对的是量级与趋势(偏差率阈值告警,缺省 ±20%,审计计数)。
2. **muniu 访问凭证管理**:muniu 管理 token 以环境变量注入(如 `KM_MUNIU_ADMIN_TOKEN`),不落库不入日志;对账拉取失败降级为报告标注「不可用」,不影响计费主链路(纯读旁路,fail-open 但显式标注)。
3. **跨仓决策记载回补**:ADR-007 补「成本对账」节,写明对 muniu HANDOFF 6b 原裁决(「kongming 实现 ingest 接收端点 + 下线自有模型计价」)的谱系修订与理由(见 design);HANDOFF 环境事实补 muniu 管理面访问条目。

## Non-goals

- **不做 muniu→kongming 直推 ingest**:muniu 计量汇为单 sink 插拔,该管道已归控制面锦官(jinguan IngestController 已落地并联调);为 kongming 再开直推需改 muniu 多 sink,且 kongming 租户身份与锦官用户身份未统一,直推将形成第二套归账真相,违反用量单源。
- **不下线自有牌价计费**:对租户收费是产品定价,与成本解耦是合理形态;本变更消除的是「无对账」,不是「双轨」本身。
- 不接锦官钱包/统一身份(长期演进方向,登记不实施)。
- 不做自动改价/自动预算联动(成本异常先可观测,再谈自动化)。

## Capabilities

### Modified Capabilities

- `developer-portal-billing`:新增成本对账要求(只读端点、量级比对语义、降级与凭证纪律)。

## Impact

- 代码:`apps/api/kongming/billing/`(对账服务+端点)、`kongming/config.py`(muniu 管理面配置)。
- 契约:管理面新增只读端点,无对外行为变更。
- 跨仓:只读依赖 muniu 既有 `GET /admin/v1/usage`(gateway-parity 已落地),muniu 侧零开发;文档层面回补 ADR-007 与 muniu HANDOFF 6b 的互引。
- 测试:对账聚合逻辑(muniu 响应解析/窗口对齐/偏差率计算/降级路径)单测;端点鉴权复用管理面既有门禁。
