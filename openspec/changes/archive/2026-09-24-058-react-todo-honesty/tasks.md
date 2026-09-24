# 058-react-todo-honesty 实施任务

## 1. 测试先行:状态机与诚实校验(宪法 Article II/III)

- [x] 1.1 todos 状态机纯函数失败测试:有证据才 DONE、失败/跳过填 reason、
      预算退出不批量 DONE、禁止无证据迁移;先跑红
- [x] 1.2 finalize 诚实断言失败测试:非 DONE 存在时全称完成措辞拒绝、
      未完成清单披露、全 DONE 放行、零回归(无 todos 文本不拦);先跑红

## 2. ReAct state 与回写

- [x] 2.1 state 增 todos;understand 多子问题建单 / 可选 plan 建单
- [x] 2.2 tool_dispatch 成功/失败/空/不适用回写状态与 evidence
- [x] 2.3 预算/超时退出钩子:保留未完成态

## 3. finalize 与回归

- [x] 3.1 finalize 对齐账本(诚实清单/禁全称措辞)
- [x] 3.2 单目标简单问零回归用例绿
- [x] 3.3 `make check` agent 子集绿;手工冒烟多子问题一失败场景,记录
      于 apply
