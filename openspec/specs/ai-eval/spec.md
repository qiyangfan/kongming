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

统计 gold 表是否在 retrieve 召回 top-K 内,输出召回率指标。

#### Scenario: 召回率统计
- GIVEN 评估集有 expected_tables
- WHEN 运行评估
- THEN 统计 gold 表在召回 top-K 的命中情况,输出 Recall@K

### Requirement: CI smoke 守护

维护小规模 smoke 集(约 5 题),CI 触发自动运行,全过才绿(防回归第一道闸)。

#### Scenario: smoke 集守护
- GIVEN smoke 集约 5 题
- WHEN CI 触发
- THEN 自动运行 smoke 评估,全过才通过

### Requirement: 成功 SQL 回写自学习闭环

成功查询回写 query_history(细则见 rag 域),作为三路召回的强 few-shot 源,形成「评估发现问题 → 知识/few-shot 精修 → 指标提升」闭环。

#### Scenario: 回写后被召回
- GIVEN 成功查询已回写
- WHEN 相似问题再提问
- THEN 历史 SQL 被召回为强 few-shot,生成质量提升可被评估集量化
