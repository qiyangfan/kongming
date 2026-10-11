# eval spec delta(096-eval-failure-attribution)

## ADDED Requirements

### Requirement: 失败归因分类

评估运行 SHALL 对每个失败用例(宽松 Execution Match 未通过、负例守护未通过、
或被全量统计捕获的异常题)给出**唯一一级分类**与分类依据。判据 SHALL 为确定性
计算(SQL 词法、既有抽表能力、链路 verdict、结果集),SHALL NOT 调用 LLM 判定
——不可复现且自身会错。分类 SHALL 为纯函数,并由常规测试门禁单测守护
(不依赖评估环境)。

一级分类与优先级序(先匹配先归类,SHALL NOT 一题多类):

1. `NEG_GUARD` 负例/拒绝守护失效(`expect_reject` / `expect_no_data` 用例失败
   即此类,不再细分);
2. `EXEC` 生成 SQL 执行失败(含 selfheal 耗尽;链路 success=false 或无 SQL);
3. `REJECT` 生成 SQL 被防火墙拦截终止(verdict=BLOCKED);
4. `ABSTAIN` 误弃答(正例题链路返回 NO_DATA);
5. `SELECT` 选表缺失——SHALL 附层拆分信号:期望表未覆盖(=召回层)或 gold 表集
   未被覆盖(=生成层);
6. `JOIN` 关联缺失(gen 涉及表已覆盖 gold 表集,但 gen 的 JOIN 数少于 gold);
7. `METRIC` 口径差异(表与 JOIN 数一致,两侧聚合函数集合不一致);
8. `FILTER` 谓词/时间窗差异(聚合一致,过滤面不一致);
9. `OTHER` 未归因。

评估统计输出 SHALL 在既有四组分数之后增列一级分类计数与 SELECT 的层拆分;
每条失败明细 SHALL 带分类名与命中的判据原文及原始信号(SHALL NOT 只给结论
不给依据)。多轮失败轮次 SHALL 以同一纯函数归因。

`OTHER` 桶 SHALL 显式报告数量与题目 id;占比 ≥20% 时,评估输出 SHALL 提示
归因规则不足需人工扩充,SHALL NOT 静默接受。

**用途边界(硬约束)**:归因结果 SHALL 用作提案立项与知识治理排序的证据面;
SHALL NOT 作为回归红线——红线仍为黄金指标劣化 ≥5pp 与提示词指纹可比性
(eval 域基线管理与 073)。启发式判据敌不过 LLM 采样波动,拿它当闸会误伤。

#### Scenario: 一题一类且带依据

- GIVEN 某失败题的表集与 JOIN 数与 gold 一致,仅聚合函数不同
- WHEN 归因执行
- THEN 归类为 METRIC,明细含两侧聚合函数集合与判据原文

#### Scenario: 选表失败拆到召回层

- GIVEN 失败题的 expected_tables 未被 gen 涉及表覆盖
- WHEN 归因执行
- THEN 归类为 SELECT 且层信号=召回层(区别于期望表已覆盖而 gold 其他表缺失
  的生成层)

#### Scenario: 负例守护失效自成类

- GIVEN expect_no_data 用例链路却生成了 SQL(哨兵未触发)
- WHEN 归因执行
- THEN 归类为 NEG_GUARD,不再按正例判据细分

#### Scenario: OTHER 超额显式提示

- GIVEN 20 题失败中 5 题落入 OTHER
- WHEN 评估统计输出
- THEN 显式报告 OTHER=5/20 与题目 id,并提示归因规则不足需人工扩充

#### Scenario: 归因不作红线

- GIVEN 某次运行 METRIC 类占比较上次上升
- WHEN 定时门禁判定回归
- THEN 红线判定 SHALL NOT 读取归因计数,仍以黄金指标劣化与指纹可比性为准

#### Scenario: 分类纯函数受默认门禁守护

- GIVEN 归因分类为纯函数
- WHEN 常规测试门禁运行(无评估环境)
- THEN 分类逻辑单测执行,判定回归即时暴露
