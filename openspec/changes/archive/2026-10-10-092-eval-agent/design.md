# Design

## Context

评估 harness 位于 apps/api/evals(进程外工具形态,经登录凭证调真实链路,
gold 直连目标库);定时门禁与基线表已存在(ai-eval 域)。088 报告、090
任务、091 协作的产出均落库(报告交付物、任务终态与步骤账本、协作账本),
文本回答(doc_answer)经问答链路可得。LLM 侧既有事实:agent 面 llm.py
openai 兼容客户端 + dashscope 直连;评估 harness 已持 LLM 凭证形态
(定时门禁 smoke 用)。约束:宪法 Article I 不引入消息队列;judge/构造
编排为 harness 进程内执行 + DB 持久化;分层纪律(域包 router→service→
models→repository,domain 零框架依赖)。

## Goals / Non-Goals

**Goals:**

- judge 与自动构造全部落 harness 侧复用既有执行形态(真实链路、真实端点)
- judge 结果与基线可比性有版本纪律(rubric 版本 + 提示词指纹双轨)
- 候选题三段闸(生成-验证-人审)产物文件化,人审走 git 提审

**Non-Goals**(设计级,proposal Non-goals 之外):

- 不做 judge 结果的产品化查询面(web/API 报表)——先以 artifacts + 库表
  满足门禁与溯源,读面待有真实使用再立项
- 不为自动构造引入「题库难度分层/知识点覆盖度量」——先保证规范与去重

## Decisions

### D1: judge 执行面放评估 harness,不放 agent 面

judge 是质量度量的编排,不是用户链路能力;放 agent 面会引入「业务链路
反向依赖评估语义」的耦合,且 agent 面零凭证原则下无基线表访问权。
harness 侧直接用 openai 兼容客户端(与 agent llm.py 同协议同路由),
评审对象经 api 查询端点/只读库连接获取——与既有评估器取数口径一致。
备选「judge 作为 agent 面新节点」被否:judge 不在问答图内,失败不应
影响用户链路,放 agent 面还得为其加 internal 端点,得不偿失。

### D2: rubric 以代码内声明式定义 + 版本常量,不落库配置面

rubric(维度/分档/权重/prompt 模板)随代码演进、随 git 可溯,落库配置面
会制造「运行时改 rubric 不改版本」的漏洞。每类对象一个 rubric 模块,
VERSION 常量随任何维度/权重/模板变更递增;judge_run 落 run 时的 rubric
版本,跨版本不可比由查询侧按版本分组保证。备选「DB 配置 + 管理面」超出
本期 Non-goal(产品化读面),刻意不做。

### D3: judge 输出用结构化 JSON 约定 + 解析失败重试一次

judge prompt 要求返回固定 JSON schema(verdict/dimensions/quotes),解析
失败保留原始输出、同 prompt 重试一次,再失败则该对象显式失败(逐对象
独立收口,不中断 run)。verdict 三档 PASS/BORDERLINE/FAIL,通过率口径
= PASS/(总对象),BORDERLINE 计入分母不计分子——与评估通过率语义对齐,
红线阈值复用 5pp 免新增口径。

### D4: 新表 judge_run / judge_result(Alembic 新迁移)

judge_run:id/对象类型/rubric 版本/提示词指纹/汇总(总数、PASS 数、
通过率)/状态;timestamptz + UUID v7。judge_result:run 外键/对象 id/
verdict/分项 JSONB/依据摘录 JSONB/失败原因。不关联具体业务表外键
(对象类型异构,存 (object_type, object_id) 二元组)——避免为四类对象
各建外键列。自动构造候选不落库:候选集即文件目录(见 D5)。

### D5: 自动构造候选落文件目录,人审 = git 提审移动

候选产物落 `apps/api/evals/datasets/candidates/<tenant>/`(yaml,逐题含
验证报告),人审通过 = 移动并入正式评估集文件并 git 提交(评审痕迹天然
可溯)。备选「候选落库 + 审核状态机」被否:评估集本体就是文件态,人审
面落库会制造文件与库双源;且 Non-goal 已排除 web 评审 UI。

### D6: 自洽验证复用 eval 域既有纯函数

规范校验(tiebreaker/业务可读列/expected_tables 同步)与执行验证
(gold 直连、行数上限)复用既有评估器校验逻辑;语义去重用既有 embedding
链路(候选题面向量 vs 既有题向量,余弦阈值缺省 0.92,超阈值标记重复)。
保证「自动构造产物符合 eval 域结构」是同一套校验器说了算,不是两套口径。

## Risks / Trade-offs

- [judge 分数波动(LLM 采样)造成红线假报] → 同 run 内对象逐个独立评审、
  样本量下限(≥3/类型);红线触发后先看依据摘录逐对象溯源,区分采样波动
  与真实劣化(与 Execution Match 逐题溯源同流程)
- [judge 与被评产出同源模型,自评偏宽] → rubric 依据摘录强制引用原文,
  人审抽查 judge 结果可回放;后续可经 multi-model-routing 换 judge 模型
  (配置项,不改架构)
- [自动构造题面同质化(LLM 生成分布窄)] → 生成 prompt 按 category 配额
  分批生成 + 语义去重拦截;人审是最终闸,同质题直接不并入
- [候选目录与正式评估集漂移(人审移动遗漏)] → 并入动作收口为一条
  harness 命令(移动+登记),不做手工 cp

## Migration Plan

新表走 Alembic 新迁移(向后兼容,纯新增);harness 新命令(judge run /
auto-construct)不触既有 smoke/full 命令行为;定时门禁扩 judge 层为
增量逻辑,评估凭证缺失时既有 smoke 层行为不变。回滚 = 门禁编排退回
smoke-only,新表留存无害。

## Open Questions

- judge 抽检对象的时间窗(近 N 天产出 vs 全量最新)——门禁编排实现期定,
  不影响契约;缺省取「自上次门禁以来的新增产出」。
