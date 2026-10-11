# Design

## Context

- 三态机制分布:config 一项(`rag_gate_mode`)+ gate 纯函数两个分支
  (off/shadow)+ service 配置单点(`_gate_configs` 返回 mode)与两处消费
  (主链路 `_apply_gate`、试跑 `recall_probe`)+ 测试(off/shadow 用例)。
- 试跑端点无 response_model、前端零消费(RagPage 零引用),删响应字段
  无破坏面;agent 面零引用;evals 零引用。
- 零知识旁路的实现形态:`knowledge_present` 为假时不执行判定(整路跳闸),
  trace 标注 `skipped=no_knowledge`——这是正确性前置(零知识≠低相关),
  不是灰度,保留。

## Goals / Non-Goals

**Goals:**

- 机制收编后代码更简:gate 纯函数单形态、配置少一项、trace 少一层
  「影子/实际」二义。

**Non-Goals:**

- 不动判据本身(双条件/按路阈值/零命中不投票/继承路同闸/阈值缺省值
  0.30/0.28 与全文档 0)。
- 不动 SQLGuard 的 mode 机制(已切主,另一域的事)。
- 不动 095 扩展与试跑端点的扩展明细透出。

## Decisions

1. **删除而非缺省切换**:保留 off「作为存在但不用的形态」会留下死分支与
   双语义 trace;展示型项目无「紧急回退开关」的真实场景(回退=改代码
   重启)。三态整体删除,`gate_hits` 单形态。
2. **trace/响应字段更名 `would_drop` → `dropped`**:影子概念消失后
   would(虚拟语气)是误导;试跑响应 `mode` 字段删除,`gate_skipped`
   与 verdict 保留(097 面板的核心消费对象)。
3. **`_gate_configs` 未注入(None)语义保留**:仅测试轻量装配形态
   (等价原 off:不判不裁),主配置恒判——config 层已无 off 可配。
4. **trace 键 `mode` 删除**:旁路 trace 收敛为 `{"skipped": "no_knowledge"}`,
   正常 trace 为两路 verdict+dropped。

## Risks / Trade-offs

- [既有召回链路测试按 off 缺省编写,恒裁后低分命中被裁] → 实施时全量
  跑 api 测试,受影响用例把桩命中分数提到阈值上(或按语义改断言为
  「被裁」),不允许为绿灯放松阈值。
- [已设 KM_RAG_GATE_MODE 的部署残留未知变量] → 配置体系对未知变量
  静默忽略(既有行为),HANDOFF 记录清理项。

## Migration Plan

单提交形态:机制删除 + 测试改写 + spec 同步;无数据迁移、无契约产物变化。
回退 = revert(机制是纯删减,revert 即恢复三态)。

## Open Questions

(无)
