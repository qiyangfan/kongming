## ADDED Requirements

### Requirement: LLM 成本对账(只读旁路)

系统 SHALL 提供管理面只读端点 `GET /system/billing/cost-reconciliation`(管理鉴权),按时间窗并列呈现产品牌价轨(`sys_llm_usage` token 总量 + `sys_invoice` 牌价账单额,整数厘)与网关成本轨(muniu 管理 API `GET /admin/v1/usage` 的 credit 汇总),计算偏差率并在超过阈值(可配,缺省 0.2)时落审计告警;两轨 MUST NOT 硬换算(牌价为产品定价,允许与成本解耦),比对对象是量级与趋势。对账为纯读旁路:任一侧不可用 MUST 降级为报告内显式「不可用」标注(HTTP 200 + 结构化标注),MUST NOT 影响计费主链路、MUST NOT 以静默空值替代。muniu 管理凭证 MUST 经环境变量注入,不落库、不入日志。

#### Scenario: 双轨正常比对

- GIVEN 窗口内两侧数据均可取
- WHEN 管理员调用对账端点
- THEN 返回双轨汇总、偏差率与数据来源标注;偏差率超阈值时产生审计告警记录

#### Scenario: 成本侧不可用降级

- GIVEN muniu 管理面不可达或未配置凭证
- WHEN 调用对账端点
- THEN 200 返回,成本侧字段显式标注「不可用」及原因,牌价轨数据正常呈现,计费链路零影响

#### Scenario: 凭证不入日志

- GIVEN 已配置 muniu 管理 token
- WHEN 任意对账请求执行与日志落盘
- THEN 日志与审计中不含 token 明文(脱敏断言)
