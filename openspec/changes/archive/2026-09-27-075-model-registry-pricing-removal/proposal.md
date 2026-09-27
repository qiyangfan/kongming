# 075-model-registry-pricing-removal 提案:移除模型注册表中从未生效的计价字段

## Why

这是一次**有规格出处、有取证、有判定**的死配置清除——不是"看着没用就删"。
溯源流程按仓内既有纪律执行,结论先行:

**规格出处**:`openspec/specs/multi-model-routing/spec.md`「多模型注册表」
Requirement 明文写着每个模型条目含「可选计价(每百万 token 单价)」。

**取证**(2026-09-27,全仓三处证据交叉):

| 检查点 | 结果 |
|---|---|
| 定义 | `apps/agent/kongming_agent/settings.py:34-35` `ModelEntry.price_input/price_output` |
| 解析 | 同文件 :120-127(JSON 取值)、:150-151(挂到条目) |
| 消费方 | `grep -rn "price_input\|price_output" apps/ contracts/ docs/` → **仅命中 settings.py 自身 6 处**,零读点 |
| 测试 | 无任何用例断言计价字段 |
| 对外文档 | `docs/model-providers.md:14` 登记的条目字段是 `name/base_url/api_key/model(+可选 protocol)`——**从未向用户承诺过计价** |

**判定**:非"为未来预留的能力",判定为**残留死配置**,理由四条:

1. **零读点**:配置它不产生任何效果。一个不生效的配置项比没有配置项更坏。
2. **信息可导出,不增信息量**:用量页已展示 tokens;成本 = tokens × 运营者
   自己设定的单价,展示其乘积不增加任何决策信息。
3. **三条成本口径已存在且刻意解耦**:055 立起两条轨并明文「两轨不做硬换算——
   牌价是产品定价,允许与成本解耦」(`billing/reconciliation.py:5`):牌价轨
   (plan 定价,用于租户账单)、muniu credit 轨(网关真实成本)。按注册表单价
   再算一轨就是**第三口径**,正是参考项目被列为坏味道的"双写口径易漂移"的
   同类问题。
4. **误导风险**:spec 承诺了计价,运维按 spec 配了 `price_input`,
   预期在算钱,实际什么都没发生。这与 058(todo 诚实)立的纪律直接冲突。

**处置**:从 spec 移除该子句,同步删除代码字段与解析。若将来确需
「按模型单价估算成本」,另立 change,且必须先回答「与牌价轨/muniu 轨如何共存」
——那是本提案拒绝在当前形态下接线的原因,不是否认需求本身。

## What Changes

1. `multi-model-routing` spec 的「多模型注册表」Requirement 移除
   「可选计价(每百万 token 单价)」子句;条目必填/可选字段清单收窄为
   名称、base_url、api_key、能力标签、protocol。
2. `settings.py` 删除 `ModelEntry.price_input/price_output` 两字段及其
   JSON 解析分支(条目中出现该键 → 静默忽略,与既有未知键同形态,不破坏
   任何既有配置)。
3. `docs/model-providers.md` 无需改动(本来就未登记计价);`evals/README.md`
   与 `.env.example` 亦无该字段登记,零连带。

## Non-goals

- **把计价接进成本轨**(第三口径):见 Why 第 3 条。若将来要做,
  起点应是先与 055 的两轨定义共存关系,而不是在注册表里加一列。
- **改造 muniu 成本轨的可用性**(直连供应商时无成本轨):属 055/ADR-007
  的演进;直连场景下运营者可自查供应商账单,不在本变更自行造估算。
- **模型注册表进 DB / 管理台管理**:ADR-002 与"配置即环境变量"的既有
  形态不变;搬进 DB 会引入第二真理源。

## Capabilities

### Modified Capabilities

- multi-model-routing:「多模型注册表」条目字段清单移除计价子句;
  注册表面其余契约(必填字段、畸形条目拒配、protocol 取值域、空表回退)
  逐字不变。

## Impact

- **落点**:`apps/agent/kongming_agent/settings.py`(删 2 字段 + 解析分支)、
  `openspec/specs/multi-model-routing/spec.md`。
- **契约**:无 OpenAPI 变更(字段不在任何契约中)。
- **向后兼容**:运营者已在 `KM_AGENT_MODEL_REGISTRY_JSON` 里写了
  `price_input/price_output` 的部署,行为从「无效但被接受」变为
  「无效且被忽略」——可观测行为完全一致,不构成破坏性变更。
- **无 schema 变更,无测试改动**(零用例断言过该字段)。
- **归档期冲突已协调**:本变更与 **080-prompt-assembly-budget** 同时
  MODIFIED「多模型注册表」——它增上下文窗口字段,本变更删计价子句。
  openspec 的 MODIFIED 块是整块替换,故两变更的该块文本已统一为**合并终态**
  (无计价 + 有 context_window),逐字一致,后 sync 者不会覆盖丢失。
  080 的取证段已同步修订,不再把 `price_*` 描述为长期现存字段。
- **072-agent-outbound-resilience**(本批另一变更)改的是同一 spec 的
  「任务级路由策略」块,与本块不相交,无冲突。
