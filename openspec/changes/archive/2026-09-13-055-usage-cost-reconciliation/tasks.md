# 055-usage-cost-reconciliation 实施任务

> 纪律:对账端点为管理面只读旁路,任何情况下不得阻塞/拖慢计费主链路;测试先行(解析/偏差率/降级路径先红后绿)。

## 1. 配置与凭证

- [x] 1.1 `kongming/config.py` 新增 muniu 管理面配置(`muniu_admin_base_url` 缺省 `http://127.0.0.1:8787`、`muniu_admin_token` 环境变量注入、`cost_reconcile_threshold` 缺省 0.2);token 不落库、不入日志(日志脱敏断言测试)。
- [x] 1.2 `.env.example` 补对应示例行(注释形态,注明纯对账旁路、不配则端点报告「未配置」)。

## 2. 对账服务(测试先行)

- [x] 2.1 muniu usage 汇总客户端:`GET /admin/v1/usage` 响应解析(分页取全/时间窗过滤/key 维度聚合),畸形响应降级「不可用」标注,先红后绿。
- [x] 2.2 本侧汇总:`sys_llm_usage` 同期 token 总量 + `sys_invoice` 牌价账单额(厘),与既有 billing 查询复用同源口径。
- [x] 2.3 偏差率计算与阈值判定(分母为零/任一侧不可用时不判偏差、显式标注);超阈值写审计计数(复用 observability 审计通道)。

## 3. 端点与契约

- [x] 3.1 `GET /system/billing/cost-reconciliation`(管理面鉴权):返回窗口内双轨汇总、偏差率、数据来源与可用性标注;muniu 不可达时 200 + 「成本侧不可用」结构化标注(不 500)。
- [x] 3.2 契约/接口测试 + 管理面鉴权回归(未授权 401/403 路径)。

## 4. 文档与跨仓记载回补

- [x] 4.1 ADR-007 补「成本对账」节:双轨定性、对 muniu HANDOFF 6b 的谱系修订(直推否决三理由,引 design)、锦官身份统一后的方案 B 演进登记。
- [x] 4.2 HANDOFF 环境事实补 muniu 管理面访问条目;开放挂账表核对无新增未决项。
- [x] 4.3 `make check` 全绿;HANDOFF 验收记录留证后归档。
