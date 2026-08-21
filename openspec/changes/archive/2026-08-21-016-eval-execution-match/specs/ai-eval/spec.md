# ai-eval 能力域 delta(016)

## MODIFIED Requirements

### Requirement: Recall@K(召回质量)

统计 gold 表是否在链路实际选表结果内,输出召回率指标——间接口径:以生成 SQL
涉及的表(抽表时排除 CTE 定义名)是否覆盖 expected_tables 判定命中(表选对
是召回生效的链路可见结果;召回中间产物不对评估器暴露)。

#### Scenario: 召回率统计

- **GIVEN** 评估集有 expected_tables
- **WHEN** 运行评估
- **THEN** 统计生成 SQL 涉及表(CTE 排除)对 expected_tables 的覆盖情况,
    输出 Recall(表命中率)指标,随基线记录

### Requirement: CI smoke 守护

维护小规模 smoke 集(约 5 题)。评估环境(双进程+数据集+LLM 凭证)非 CI
标配,smoke 集 SHALL 以显式触发方式在具备评估环境处运行(本地/预发手动),
全过才视为该环境基线可信;匹配器与评估集加载逻辑为确定性纯函数,SHALL 由
常规测试门禁单测守护(不依赖评估环境,CI 可跑)。

#### Scenario: smoke 集守护

- **GIVEN** smoke 集约 5 题,评估环境就绪
- **WHEN** 显式触发 smoke 评估
- **THEN** 全过才绿(防回归第一道闸),结果记录进基线表

#### Scenario: 匹配器逻辑常规门禁守护

- **GIVEN** 匹配器为纯函数(严格/宽松/投影/数值子集)
- **WHEN** 常规测试门禁运行
- **THEN** 匹配器判定逻辑单测执行(不依赖评估环境),回归即时暴露
