# Feat: 043 生成纪律(知识未覆盖如实说明,根除 LLM 凭先验编表)

## Why

2026-08-29 事故(default 租户 + default 源问「统计每个员工处理的租赁总数」)
经 042 期定性:知识层无辜(召回隔离/few-shot 通道全干净),**真根因在生成层**——
知识未覆盖时 LLM 凭训练先验编造表名。复现实锤:同题在 default 租户重问,
LLM 编出 `FROM public.staff JOIN public.rental`(sakila 经典示例库先验),
执行连败 4 次(初始 1 + 自愈 3,sys_sql_audit 可溯),自愈喂回「does not
exist」后仍同因编造。

两个 prompt 缺口叠加:

1. `_EMPTY_HINT`(schema_hint 为空时的占位文案)写着「请基于问题尽力生成」
   ——无表结构时的「尽力」就是教唆编表;
2. `SQL_GEN_SYSTEM` 九条规则全部在教「怎么写 SQL」,没有任何一条约束
   「表/列只能来自可用表结构段」,更没有「知识未覆盖须如实说明」的出路。

风险不止报错:幻觉表名若与执行库可达范围(public 白名单)碰同名,LLM 会
**静默查错库的表返回错误数据**——比报错更糟的正确性风险。

## What Changes

- **`_EMPTY_HINT` 文案重写**:空结构 =「未检索到相关表结构 + 严禁臆测
  表名/列名 + 所需数据不在范围时只输出哨兵」,替换「尽力生成」教唆。
- **`SQL_GEN_SYSTEM` 新增规则 10(表范围纪律)**:表与列 MUST 仅来自
  「可用表结构」段(含表关系/历史 SQL 参考段);该段未覆盖问题所需数据时,
  **唯一例外于规则 7**,只输出哨兵 `NO_RELEVANT_TABLE`,不解释不编造。
- **generate 节点哨兵短路**:识别哨兵(strip+大小写折叠,防 markdown
  包裹)即终止——不进 validate/execute、不进自愈、零执行零重试;置
  `end_reason="no_data"` 走既有 fail 终态,`error` 为用户友好文案。
  `generate→validate` 固定边改条件边。
- **自愈喂回增强**:上次错误含 `does not exist`(大小写不敏感)时,喂回
  文案追加「所引表不在可用表结构清单中,清单确无所需数据只输出
  NO_RELEVANT_TABLE」引导,杜绝同因连败空转烧 LLM 配额。
- **prompt 资产纪律**:prompts.py 文件头例外清单登记本次增量;
  test_prompts.py 断言同步。
- 测试先行:prompt 断言/哨兵短路/喂回增强/既有链路零回归。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `nl2sql-agent`:生成环节 MUST 具备表范围纪律——可用表结构未覆盖问题
  所需数据时,生成 MUST 以哨兵短路如实说明(零执行零重试),SHALL NOT
  臆测表名;selfheal 对「表不存在」类错误喂回 MUST 附清单引导。

## Non-goals

- **react 内核(019/Bot 渠道)**:工具调用环有执行结果反馈,编表失败由
  工具错误自然回灌;其纪律段改造(2026-08-28 has_tools 已动过)独立评估;
- **no_data 的中性呈现形态**:短路后走既有 FAILED+友好 error 文案(web
  错误横幅),不做「如实回答」一等中性卡片——需要新增消息状态枚举(库
  CHECK+web 分支+统计口径牵连),登记为条件触发(用户反馈形态误导再升级);
- **空结果(查询成功但 0 行)**:属数据事实,与本变更无关;
- **chat 三态映射与 HTTP 契约**:SUCCESS/REJECTED/FAILED 与 QueryOut
  字段结构零改动(error 文案属内容非契约)。
