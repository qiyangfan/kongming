# Tasks

> 勾选补记(2026-10-14 账面清偿):全部条目经代码现实核验后补勾——
> `rag_gate_mode` 全仓零残留(grep 实证)、恒裁剪形态在案(HANDOFF 064 节)、
> 097 探针面板已兑现(recall-probe-panel.tsx)、本变更已归档。

## 1. 机制收编(api)

- [x] 1.1 gate 纯函数单形态(删 mode 参数与 off/shadow 分支,恒裁剪+
  verdict 恒产出;docstring 更新),验证:改写后的判定纯函数测试绿
- [x] 1.2 config 删 `rag_gate_mode` 与三态注释(恒生效口径),service
  配置单点与主链路/试跑两处消费收敛(trace 键 mode 删、`*_would_drop`
  → `*_dropped`;试跑响应删 mode 字段),验证:`make api-check` rag 子集绿
- [x] 1.3 测试改写(off/shadow 用例删除;恒裁语义断言;试跑同源/旁路/
  继承路用例适配;既有召回链路低分桩用例按语义修正),验证:
  全量 `pytest apps/api` 绿,无跳过放松

## 2. 同步与收尾

- [x] 2.1 097 提案工件同步(面板展示「判定结论+零知识标注」,删模式/
  影子措辞),验证:097 validate 绿
- [x] 2.2 HANDOFF 更新(064 现状段=恒生效收编;下一棒清单删切换项;
  环境变量清理提示),验证:文档自查(无过程叙事)
- [x] 2.3 `make check` api+agent 子集全绿;归档 106(spec sync)
