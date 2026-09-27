# ai-eval spec delta(073-prompt-fingerprint)

## ADDED Requirements

### Requirement: Prompt 版本指纹

生效的提示词资产全集 SHALL 有确定性的内容指纹(对资产文本取密码学哈希所得)。指纹 SHALL 满足:同一资产内容在任意进程、任意次运行中计算所得一致;任一资产字变化即产生不同指纹;不含任何人工维护的序号或随机分量。评估运行 SHALL 取得并记录该指纹;评估产物 SHALL 在跨越不同指纹的两次运行之间显式标注「通过率不可直接比较」,SHALL NOT 静默套用劣化红线。

#### Scenario: 指纹确定性

- GIVEN 同一提示词资产内容
- WHEN 在两个独立进程中分别计算指纹
- THEN 两次结果逐字符一致

#### Scenario: 资产变更即换指纹

- GIVEN 任一提示词资产文件内容发生变更
- WHEN 重新计算指纹
- THEN 得到与变更前不同的指纹

#### Scenario: 跨版本比对显式标注

- GIVEN 本次评估与上次基线记录的指纹不同
- WHEN 输出评估结果
- THEN 结果中显式标注「跨提示词版本,通过率不可直接比较」并列出两侧指纹
- AND 不静默按劣化阈值判定回归

## MODIFIED Requirements

### Requirement: CI smoke 守护

维护小规模 smoke 集(约 5 题)。评估环境(双进程+数据集+LLM 凭证)SHALL
NOT 作为 PR 门禁;smoke 集 SHALL 支持两种运行形态:

- **显式触发**:在具备评估环境处(本地/预发)手动运行,全过才视为该环境
  基线可信;
- **定时门禁**:系统 SHALL 提供按周期自动运行的回归门禁,在同一评估环境
  跑 smoke 层,严格/宽松通过率 SHALL 落 artifacts 并与上一次运行可比。
  定时门禁 SHALL 以回归红线为失败判据(劣化 ≥5pp 逐题溯源),失败即门禁
  失败;评估凭证缺失时 SHALL 显式失败,SHALL NOT 静默跳过(静默跳过等于
  没有门禁)。

匹配器与评估集加载逻辑为确定性纯函数,SHALL 由常规测试门禁单测守护
(不依赖评估环境,CI 可跑)。

**可重复口径**:prompt 类改动后重跑前,SHALL 清理同题问答沉淀与召回
上下文缓存,否则验证的是缓存而不是 prompt。

**版本可比性**:评估产物 SHALL 记录当次运行的提示词版本指纹;两次运行
指纹不同时 SHALL 显式标注「跨提示词版本,通过率不可直接比较」并列出
两侧指纹,SHALL NOT 静默套用劣化红线。

#### Scenario: smoke 集守护

- GIVEN smoke 集约 5 题,评估环境就绪
- WHEN 显式触发 smoke 评估
- THEN 全过才绿(防回归第一道闸),结果记录进基线表(含提示词版本指纹)

#### Scenario: 定时门禁跑 smoke 层

- GIVEN 评估环境与 LLM 凭证已配置于定时门禁
- WHEN 定时门禁到达运行点
- THEN 自动执行 smoke 层,通过率落 artifacts,并与上一次运行可比

#### Scenario: 回归红线失败

- GIVEN 本次 smoke 严格/宽松通过率较上一次劣化 ≥5pp,且两次运行提示词
  版本指纹相同
- WHEN 定时门禁完成
- THEN 门禁失败,产出可逐题溯源的信息

#### Scenario: 跨版本比对显式标注

- GIVEN 本次评估与上次基线记录的提示词版本指纹不同
- WHEN 输出评估结果
- THEN 显式标注「跨提示词版本,通过率不可直接比较」并列出两侧指纹
- AND 不静默按劣化阈值判定回归

#### Scenario: 凭证缺失显式失败

- GIVEN 定时门禁缺少评估用 LLM 凭证
- WHEN 定时门禁运行
- THEN 显式失败并提示缺凭证,SHALL NOT 静默跳过

#### Scenario: 重跑前清缓存

- GIVEN 上一轮已产生同题问答沉淀与召回上下文缓存
- WHEN 因 prompt 改动重跑 smoke
- THEN 先清理沉淀与缓存再运行,验证对象为当前 prompt

#### Scenario: 匹配器逻辑常规门禁守护

- GIVEN 匹配器为纯函数(严格/宽松/投影/数值子集)
- WHEN 常规测试门禁运行
- THEN 匹配器判定逻辑单测执行(不依赖评估环境),回归即时暴露

> 备注(并行变更协调):本块为 065-eval-ci-gate 与 073-prompt-fingerprint
> 对「CI smoke 守护」的**合并终态**。openspec MODIFIED 为整块替换,两变更
> 的本块文本刻意保持逐字一致——无论谁后 sync 进 `specs/ai-eval/`,结果都
> 同时包含「定时门禁」与「指纹可比性」,不会互相覆盖丢失。
