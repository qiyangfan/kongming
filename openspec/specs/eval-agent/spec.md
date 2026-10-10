# eval-agent Specification

## Purpose
评测智能体域:LLM-as-judge 对非确定性产出(报告/任务结论/协作裁决/文本回答)
按 rubric 结构化评分并纳入基线红线;LLM 自动构造评估集候选(生成-自洽验证-
人审三段闸)持续扩充回归题面——五大方向统一的质量闸门。

## Requirements

### Requirement: judge 评审对象与 rubric 声明

judge SHALL 以只读方式评审四类非确定性产出:报告(摘要与洞察段)、任务
(最终结论与交付物)、协作(worker 裁决与汇总结论)、文本回答(知识/融合
问答的回答正文)。每类对象 SHALL 声明式定义 rubric(评审维度、每维整数
分档、维度权重),rubric 变更 SHALL 递增版本号;跨 rubric 版本的分数
SHALL NOT 直接比较,SHALL 显式标注两侧版本(与提示词版本指纹同纪律)。
judge 评审 SHALL NOT 对评审对象产生任何写副作用。

#### Scenario: 报告产出按 rubric 评审

- **GIVEN** 一份已完成的报告交付物(088)在库
- **WHEN** 对该报告运行 judge
- **THEN** 按报告 rubric 输出各维度分档与 verdict,报告本体字段零改动

#### Scenario: rubric 版本升级后不可比

- **GIVEN** 报告 rubric 由 v1 调整为 v2(维度或权重变更)
- **WHEN** v2 首轮 judge 结果与历史 v1 结果并列
- **THEN** 显式标注「跨 rubric 版本,分数不可直接比较」并列出两侧版本号,
    不静默套用劣化红线

### Requirement: judge 运行与结果收口

单次 judge run SHALL 显式指定评审范围(对象类型 × 对象集合),逐对象输出
结构化结果:verdict(PASS/BORDERLINE/FAIL)、各维度分档、依据摘录(引自
产出原文、可人工复核的关键片段);run 级通过率与逐对象结果 SHALL 落库。
judge LLM 调用失败、超时或输出不可解析时,该对象 SHALL 显式失败并保留
原始输出供排查,SHALL NOT 静默跳过或计为通过;单对象失败 SHALL NOT 中断
同 run 其余对象的评审(逐对象独立收口)。

#### Scenario: 结构化结果与依据摘录落库

- **GIVEN** 一次 judge run 覆盖 3 个评审对象
- **WHEN** run 完成
- **THEN** 每对象落 verdict/分项分/依据摘录,run 落汇总通过率与 rubric 版本

#### Scenario: 输出不可解析显式失败

- **GIVEN** judge LLM 对某对象返回不可解析内容
- **WHEN** run 收口
- **THEN** 该对象标记评审失败并保留原始输出,其余对象照常评审,
    run 汇总中失败对象计入分母、SHALL NOT 计为通过

### Requirement: judge 通过率进基线红线

judge run 通过率 SHALL 作为独立基线维度记录(按对象类型分组);同 rubric
版本、同对象类型下,通过率较前一基线劣化 ≥5pp SHALL 触发回归红线(逐对象
溯源后才可合入)——与 Execution Match 基线同口径。SQL 正确性 SHALL NOT
由 judge 判定(仍走 eval 域 Execution Match 确定性口径)。

#### Scenario: judge 通过率劣化触发红线

- **GIVEN** 报告类 judge 前基线通过率 90%(rubric v1)
- **WHEN** 同 rubric v1 新一轮通过率 82%
- **THEN** 触发红线,须逐对象溯源(区分 rubric 漂移与真实产出劣化)后才可合入

### Requirement: 评测集自动构造(生成-验证-人审)

系统 SHALL 支持按租户+数据源从知识面(schema 表说明/字段语义/示例 SQL 与
文档知识)自动生成评估候选题:question + gold_sql + expected_tables +
category,题类覆盖 eval 域既有 category 分类。每条候选 SHALL 过自洽验证:
gold_sql 直连目标库执行成功、结果集 ≤1000 行、gold 编写规范校验
(tiebreaker/业务可读列/expected_tables 同步)、与既有题及同批候选语义
去重;候选连同验证报告(逐项校验结果与失败原因)落候选集目录。人审通过
是唯一并入正式评估集的路径,SHALL NOT 自动并入;候选并入后 SHALL 重跑
全量评估重建基线(题面已变)。

#### Scenario: 候选生成与自洽验证报告

- **GIVEN** 目标数据源 schema 知识与文档知识在库
- **WHEN** 运行自动构造
- **THEN** 生成候选题逐条执行自洽验证,连同验证报告落候选集目录,
    报告含执行行数、规范校验清单、语义去重判定

#### Scenario: 规范违例候选被拦

- **GIVEN** 某候选 gold_sql 含 ORDER BY ... LIMIT 而无 tiebreaker
- **WHEN** 自洽验证
- **THEN** 该候选验证 FAIL 并携失败原因落目录,人审界面不可将其并入

#### Scenario: 语义重复候选被标记

- **GIVEN** 某候选与既有评估集某题语义高度相似
- **WHEN** 自洽验证
- **THEN** 该候选被标记重复,默认不进入可并入清单

#### Scenario: 人审并入并重建基线

- **GIVEN** 人审将 5 条候选并入正式评估集
- **WHEN** 并入完成
- **THEN** 正式评估集扩题,重跑全量评估并重建基线(标注「自动构造扩题」原因)
