# ai-eval 能力域

## Purpose

质量度量与自学习闭环的地基:知识元数据质量(字段中文语义保留、示例段隔离)、few-shot 激活、评估集与自动评分(Execution Match 黄金指标 + Recall@K)、CI smoke 守护。

## Requirements

### Requirement: 知识元数据质量(字段语义完整)

知识条目的 schema 提示拼接时保留字段中文说明;markdown 解析隔离「示例段」不污染「字段段」。

#### Scenario: schema 提示保留字段中文说明
- GIVEN 知识条目 columns_comment = {"customer_id":"客户ID","email":"邮箱"}
- WHEN 拼接 schema 提示
- THEN 输出含「customer_id(客户ID),email(邮箱)」而非裸列名,LLM 能理解字段语义

#### Scenario: 解析隔离示例段不污染字段段
- GIVEN 上传含「字段: id 主键 / 示例: SELECT COUNT(*) FROM tbl」的 markdown
- WHEN 解析
- THEN columns_comment 只含 {"id":"主键"},不含 SQL 片段;示例 SQL 单独入 sample_queries

### Requirement: few-shot 激活

sample_queries 非空时 generate 提示含 few-shot 段(参考示例 SQL);为空时不加该段(降级兼容)。

#### Scenario: few-shot 注入提示
- GIVEN 知识条目含示例 SQL
- WHEN 用户提问
- THEN generate 提示含 few-shot 段
- AND 为空时不加(不报错)

### Requirement: Execution Match(黄金指标)

gold_sql 与生成 SQL 都在目标库真实执行,对比结果集判定 pass/fail(排序无关集合相等为基础口径,细则见 eval 域)。

#### Scenario: 双路径执行比较
- GIVEN 评估集有 gold_sql
- WHEN 运行评估
- THEN gold_sql 与生成 SQL 都真实执行,比较结果集判定(非字符串相似度)

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

### Requirement: 成功 SQL 回写自学习闭环

成功查询回写 query_history(细则见 rag 域),作为三路召回的强 few-shot 源,形成「评估发现问题 → 知识/few-shot 精修 → 指标提升」闭环。

#### Scenario: 回写后被召回
- GIVEN 成功查询已回写
- WHEN 相似问题再提问
- THEN 历史 SQL 被召回为强 few-shot,生成质量提升可被评估集量化
