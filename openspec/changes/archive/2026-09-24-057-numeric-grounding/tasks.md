# 057-numeric-grounding 实施任务

## 1. 测试先行:领域纯函数(宪法 Article II/III)

- [x] 1.1 白名单构建与归一化失败测试:结果集数值/百分数/千分位/1 与 1.0
      等价、结构性常量(行数/预览 N)入集;先跑红
- [x] 1.2 数值审计剥离失败测试:无白名单数字句剥离、合法句保留、
      insights 同口径;先跑红
- [x] 1.3 compute_delta/compute_share 失测试:正确公式、入参不在白名单
      拒绝、返回值入白名单;先跑红

## 2. stats 预计算与 compute 工具

- [x] 2.1 compute_stats 扩展常见派生(可选环比/占比),失败不产出
- [x] 2.2 两工具注册进内核工具表(write=false),单测覆盖
- [x] 2.3 跑 agent 测试子集,1.x 转绿

## 3. summarize 收口

- [x] 3.1 summarize 后处理:白名单构建→审计剥离→输出;零派生违规时
      与原输出逐字一致(零回归)
- [x] 3.2 SUMMARIZE_SYSTEM 规则增量 + test_prompts 断言同步
- [x] 3.3 ReAct finalize 同口径(有 insights/summary 时过同一审计纯函数)

## 4. 门禁与冒烟

- [x] 4.1 `make check` agent 子集绿
- [x] 4.2 手工冒烟:构造两行增长率问法,核对要么来自 compute/预计算、
      要么无该表述,绝不出现心算错值;记录于 apply
