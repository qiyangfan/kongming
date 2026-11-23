# Wiki 问答正式对照报告(2026-11-23)

- 题集:formal_wiki.yaml(26 题:single 10 / multi 10 / conflict 3 / no_answer 3,含标准答案与必需事实)
- 主题页:七页,覆盖题集涉及的全部 9 份文档(两页原型 + 补五页,均过审核门禁;维护时间合计 85 分钟,上限 120;6 份设备/渠道类文档为运行后补入,不涉 wiki 题集)
- 三种方式同题配对;盲评去标签并携带标准答案与引用原文;资料运行前后核对无变化
- 运行形态:本地最小栈(api + agent),上游 maas 镜像端点 `qwen-plus` 别名;回答与评审同模型;`debug=true` 标注仓库合成语料
- 结论:keep(维持原文检索);逐题判定 78 条;wiki_first 转用原文 0 次
- 费用:回答 0.0675 CNY + 评审 0.0749 CNY(价目版本 2026-11,单价 0.8/2.0 CNY 每百万 token);单题最高 0.0059 CNY(上限 0.2);缺用量 0

## 分方式汇总

| 方式 | n | 正确率 | 有据正确率 | 引用支持率 |
|---|---|---|---|---|
| raw | 26 | 23/26 88.5% | 88.5% | 96.2% |
| wiki_first | 26 | 21/26 80.8% | 80.8% | 80.8% |
| parallel | 26 | 21/26 80.8% | 80.8% | 80.8% |

## 分题型正确率(判定分桶)

| 方式 | 跨资料(multi,10) | 单资料(single,10) | conflict/no_answer(6) |
|---|---|---|---|
| raw | 90.0%(9/10) | 100.0%(10/10) | 4/6 |
| wiki_first | 90.0%(9/10) | 100.0%(10/10) | 2/6 |
| parallel | 90.0%(9/10) | 100.0%(10/10) | 2/6 |

## 判定原因

- wiki_first: 引用支持率 80.8% 低于 95%
- wiki_first: 跨资料题正确率改善 +0.0% 低于 5%
- parallel: 引用支持率 80.8% 低于 95%
- parallel: 跨资料题正确率改善 +0.0% 低于 5%

## 逐题判定

| 题号 | 方式 | correct | grounded | cited |
|---|---|---|---|---|
| w_c01 | raw | 0 | 0 | 0 |
| w_c01 | wiki_first | 0 | 0 | 0 |
| w_c01 | parallel | 0 | 0 | 0 |
| w_c02 | raw | 1 | 1 | 1 |
| w_c02 | wiki_first | 0 | 0 | 1 |
| w_c02 | parallel | 0 | 0 | 1 |
| w_c03 | raw | 1 | 1 | 1 |
| w_c03 | wiki_first | 0 | 0 | 0 |
| w_c03 | parallel | 0 | 0 | 0 |
| w_m01 | raw | 1 | 1 | 1 |
| w_m01 | wiki_first | 1 | 1 | 0 |
| w_m01 | parallel | 1 | 1 | 1 |
| w_m02 | raw | 1 | 1 | 1 |
| w_m02 | wiki_first | 1 | 1 | 1 |
| w_m02 | parallel | 1 | 1 | 1 |
| w_m03 | raw | 1 | 1 | 1 |
| w_m03 | wiki_first | 1 | 1 | 1 |
| w_m03 | parallel | 1 | 1 | 1 |
| w_m04 | raw | 0 | 0 | 1 |
| w_m04 | wiki_first | 0 | 0 | 0 |
| w_m04 | parallel | 0 | 0 | 0 |
| w_m05 | raw | 1 | 1 | 1 |
| w_m05 | wiki_first | 1 | 1 | 1 |
| w_m05 | parallel | 1 | 1 | 1 |
| w_m06 | raw | 1 | 1 | 1 |
| w_m06 | wiki_first | 1 | 1 | 1 |
| w_m06 | parallel | 1 | 1 | 1 |
| w_m07 | raw | 1 | 1 | 1 |
| w_m07 | wiki_first | 1 | 1 | 1 |
| w_m07 | parallel | 1 | 1 | 1 |
| w_m08 | raw | 1 | 1 | 1 |
| w_m08 | wiki_first | 1 | 1 | 1 |
| w_m08 | parallel | 1 | 1 | 1 |
| w_m09 | raw | 1 | 1 | 1 |
| w_m09 | wiki_first | 1 | 1 | 1 |
| w_m09 | parallel | 1 | 1 | 1 |
| w_m10 | raw | 1 | 1 | 1 |
| w_m10 | wiki_first | 1 | 1 | 1 |
| w_m10 | parallel | 1 | 1 | 1 |
| w_n01 | raw | 1 | 1 | 1 |
| w_n01 | wiki_first | 1 | 1 | 1 |
| w_n01 | parallel | 1 | 1 | 1 |
| w_n02 | raw | 0 | 0 | 1 |
| w_n02 | wiki_first | 0 | 0 | 0 |
| w_n02 | parallel | 0 | 0 | 0 |
| w_n03 | raw | 1 | 1 | 1 |
| w_n03 | wiki_first | 1 | 1 | 1 |
| w_n03 | parallel | 1 | 1 | 1 |
| w_s01 | raw | 1 | 1 | 1 |
| w_s01 | wiki_first | 1 | 1 | 1 |
| w_s01 | parallel | 1 | 1 | 1 |
| w_s02 | raw | 1 | 1 | 1 |
| w_s02 | wiki_first | 1 | 1 | 1 |
| w_s02 | parallel | 1 | 1 | 1 |
| w_s03 | raw | 1 | 1 | 1 |
| w_s03 | wiki_first | 1 | 1 | 1 |
| w_s03 | parallel | 1 | 1 | 1 |
| w_s04 | raw | 1 | 1 | 1 |
| w_s04 | wiki_first | 1 | 1 | 1 |
| w_s04 | parallel | 1 | 1 | 1 |
| w_s05 | raw | 1 | 1 | 1 |
| w_s05 | wiki_first | 1 | 1 | 1 |
| w_s05 | parallel | 1 | 1 | 0 |
| w_s06 | raw | 1 | 1 | 1 |
| w_s06 | wiki_first | 1 | 1 | 1 |
| w_s06 | parallel | 1 | 1 | 1 |
| w_s07 | raw | 1 | 1 | 1 |
| w_s07 | wiki_first | 1 | 1 | 1 |
| w_s07 | parallel | 1 | 1 | 1 |
| w_s08 | raw | 1 | 1 | 1 |
| w_s08 | wiki_first | 1 | 1 | 1 |
| w_s08 | parallel | 1 | 1 | 1 |
| w_s09 | raw | 1 | 1 | 1 |
| w_s09 | wiki_first | 1 | 1 | 1 |
| w_s09 | parallel | 1 | 1 | 1 |
| w_s10 | raw | 1 | 1 | 1 |
| w_s10 | wiki_first | 1 | 1 | 1 |
| w_s10 | parallel | 1 | 1 | 1 |

## 说明

- 复跑稳定性(同日同配置第二轮):结论 keep 不变;wiki_first/parallel 总正确率两轮均为 21/26,跨资料两轮均为 9/10(raw 同为 9/10,改善 +0.0%);引用支持两轮 80.8%/84.6%,均低于 95%。raw 在 conflict/no_answer 波动(4/6→1/6),该两类不进判定桶,不影响结论。
- 题型口径:multi 桶按题集定义为「跨资料或多段」,其中严格跨文档 6 道(m01/m02/m04/m06/m07/m08),其余 4 道(m03/m05/m09/m10)可由单文档作答;严格跨文档子集上 raw 与 wiki_first 同为 5/6(同漏 m04),结论不受桶构成影响。
- 覆盖修复效果:单资料题从首轮 50% 回到 10/10(与 raw 持平,回归消除);跨资料题从 40% 升到 90%,与 raw 持平。但登记条件要求跨资料至少改善 5 个百分点,持平不达标——这一条 keep 原因与覆盖率无关。
- 引用支持率口径:提交给评审的是整批证据,要求逐条支持结论。主题页方式每题证据 12–15 条,原文路 5 条;外围页面稀释引用支持。即使改按「回答实际引用的证据」评审(最好情形 22/26=84.6%),仍低于 95%,keep 不受该口径影响。
- conflict/no_answer 两类不进判定桶:wiki_first 在冲突题 w_c02/w_c03 由 raw 的对变为错,是总正确率差距的主要来源。
- 运行沿革:首轮两页原型(覆盖 4/9 文档)的 keep 结论是覆盖不足的机械结果,由七页全覆盖重跑取代;判定条件、题集、费用与维护上限均沿用运行前登记值。
