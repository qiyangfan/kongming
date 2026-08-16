# nl2sql-agent 能力域

## Purpose

NL2SQL 核心链路的能力深化:多轮上下文全链路打通(前端→控制面→Agent history 透传 + understand 指代消解)、SQL 生成质量规则(join 连接条件/聚合维度/方言/时间窗)、结构化洞察(summary + insights + stats,失败降级纯文本)、Schema 提示由 RAG 智能选取取代全表倾倒。

## Requirements

### Requirement: 多轮上下文链路打通(命脉级)

前端攒最近 5 轮 user 问句,控制面全链路透传 history 到 Agent 面,understand 节点用历史做指代消解与省略补全;不传 history 时向后兼容。

#### Scenario: 前端攒历史问句传后端
- GIVEN 用户已进行 2 轮对话
- WHEN 发起第 3 轮提问
- THEN 查询请求体含最近 5 轮 user 问句的 history 列表

#### Scenario: 控制面全链路透传 history 到 Agent
- GIVEN 查询请求含非空 history
- WHEN 控制面调用 Agent 面
- THEN Agent 请求体含相同 history,注入 AgentState

#### Scenario: understand 节点用历史做指代消解
- GIVEN history 非空,当前问句含指代词(「这些」/「它们」/「上面提到的」)
- WHEN understand 节点执行
- THEN 结合历史问句输出补全指代后的清晰问句,并传入 generate 节点

#### Scenario: 多轮连续查询上下文延续
- GIVEN 第 1 轮「查询租金最高的 5 部影片」已返回结果
- WHEN 第 2 轮问「这些影片各自的分类是什么」
- THEN 生成的 SQL 引用第 1 轮的影片范围(通过指代消解),执行成功

#### Scenario: 契约向后兼容
- GIVEN 调用方不传 history
- WHEN 查询
- THEN Agent 用空 history 正常工作,不报错

#### Scenario: history 长度上限保护
- GIVEN 前端传了 10 轮历史
- WHEN understand 节点读取
- THEN 只取最近 5 轮,不因 history 过长导致 prompt token 爆炸

### Requirement: SQL 生成质量规则

generate 节点的系统提示必须强化:多表 JOIN 用显式连接条件(非隐式逗号 join)、聚合维度识别(GROUP BY + AVG/COUNT)、PostgreSQL 方言(ILIKE)、时间窗用 interval/DATE_TRUNC(非硬编码日期);且不影响单表简单查询。

#### Scenario: 多表 join 连接条件推导
- GIVEN 问题「查询每个客户租过的所有影片」(需 4 表连接)
- WHEN generate 节点执行
- THEN SQL 用显式 JOIN + ON 连接条件,基于外键关系

#### Scenario: 聚合维度识别
- GIVEN 问题「每个影片分类的平均租金」
- WHEN generate 节点执行
- THEN SQL 含 GROUP BY + AVG,统计查询不加行数限制截断语义

#### Scenario: PostgreSQL 方言正确使用
- GIVEN 问题「查询邮箱包含 smith 的客户」
- WHEN generate 节点执行
- THEN SQL 用 ILIKE 或 LOWER() + LIKE(大小写不敏感)

#### Scenario: 时间窗表达
- GIVEN 问题「上个月租借的影片」
- WHEN generate 节点执行
- THEN SQL 用 interval 或 DATE_TRUNC 表达相对时间

#### Scenario: 单表查询不过度复杂化
- GIVEN 问题「查询所有客户邮箱」
- WHEN generate 节点执行
- THEN SQL 仍是简单单表查询(强化规则不影响简单场景)

### Requirement: 结果智能分析(结构化洞察)

summarize 节点输出结构化洞察:summary(一句话)+ insights[](聚合洞察 max/min/ratio、数据质量提示)+ stats;LLM 返回非 JSON 时降级为纯文本。

#### Scenario: 聚合结果结构化洞察
- GIVEN 查询返回分组聚合结果
- WHEN summarize 节点执行
- THEN 返回 JSON 含 summary + insights(如 [{type: "max", text: "动作类最多 120 部"}])+ stats
- AND 前端渲染洞察卡片(不只一句话)

#### Scenario: 明细结果数据质量提示
- GIVEN 查询返回明细结果且某列有空值
- WHEN summarize 节点执行
- THEN insights 含 {type: "quality", text: "email 列有 3 个空值"}

#### Scenario: LLM 返回非 JSON 降级
- GIVEN summarize 节点 LLM 返回纯文本
- WHEN 解析失败
- THEN 退化为 summary=该文本,insights/stats 为空,不报错

### Requirement: Schema 智能选取

retrieve 节点用 RAG 召回结果做 schema 智能选取,不维护全表硬编码提示;多数据集场景提示收窄到召回相关表;RAG 完全失败时降级为空提示(非全表)。

#### Scenario: 多数据集场景提示收窄
- GIVEN 数据集有 11 张表,问题只涉及 2 表
- WHEN retrieve 节点拿到 RAG Top-K 召回(K=5)
- THEN 喂给 generate 的 schema 提示只含相关表(≤5 张),SQL 通过率不降

#### Scenario: RAG 完全失败的安全降级
- GIVEN RAG 召回服务不可用
- WHEN retrieve 节点降级
- THEN 返回空 schema 提示,提示 LLM「无法获取表结构」
- AND 可能生成失败 SQL 是可接受的降级(不误导,不硬编码全表)

### Requirement: selfheal 错误分类(省 LLM 配额)

selfheal 按错误类型分类:权限/防火墙拦截类直接终止不重试;语法/执行错误才重试,节省 LLM 配额。

#### Scenario: 权限拦截不重试
- GIVEN 生成的 SQL 被防火墙拦截(FORBIDDEN_DML)
- WHEN 路由决策
- THEN 直接终止,不进入 selfheal,不浪费 LLM 重试配额

#### Scenario: 语法错误仍重试
- GIVEN 生成 SQL 有语法错误(非权限类)
- WHEN execute 失败
- THEN 进入 selfheal 重试(retry_count < MAX_RETRY),重试耗尽才终止
