# Feat: 046 SQL 投影 id 收敛(技术主键默认不进投影)

## Why

018 投影护栏的两条既有激励在明细列举场景复合出反效果:规则 3「明细列举必须
附带能标识行的名称列……ID/编号列不能替代名称列(ID 可附加)」+ 规则 6「排序
键并列时追加主键等唯一列作第二排序键」——模型把「ID 可附加」泛化成「明细就
该带 ID」,把技术主键(film_id/rental_id 等代理键)塞进 SELECT 投影。用户在
2026-08-29 会话点名此形态(用户看到的结果表里是看不懂的代理键数值),且与
规则 3 自己的「除标识列与点名信息项外不要随意加列」自相矛盾。

两个具体病灶:

1. **技术主键进投影**:对用户不可读、对问题无信息量(问「列出影片标题」
   多一列 film_id 纯噪音);护栏原文「ID 可附加」给了模型合法外衣。
2. **规则 6 tiebreaker 与 DISTINCT 的硬冲突**:`SELECT DISTINCT` 下
   ORDER BY 列必须在投影内(PG 报错 for SELECT DISTINCT, ORDER BY
   expressions must appear in select list);若为满足它把主键塞进投影,
   主键逐行唯一,DISTINCT 去重立即失效——两条路都是错的,规则 6 对
   DISTINCT 去重查询必须显式例外。

## What Changes

- **规则 3 收紧**:技术主键等代理键(如 `*_id`)默认不进投影——用户未点名
  时投影不含 ID 列,用户明确点名 ID/编号时才投影(点名不拦);行标识需要
  机器可核对编号时,优先业务编号列(订单号/编号类列,本就面向用户)。
- **规则 6 例外句**:DISTINCT 去重查询不适用「追加主键等唯一列
  tiebreaker」——排序键只能取已在投影中的列(业务列),并列可能让位于去重
  语义;附 PG 硬约束依据。
- **反例二增一校准**:新增「DISTINCT 去重查询追加投影外主键排序」与
  「问句口径所需列不在表结构中时用其他列硬凑」两条反例;既有「ID 可附加」
  措辞与收紧后口径冲突,同步校准。
- **prompt 资产纪律**:SQL_GEN_SYSTEM 为上游逐字保值资产(已有 018/043
  例外登记),本次增量登记 prompts.py 文件头例外清单;test_prompts.py
  关键句断言同步(既有「ID/编号列不能替代名称列」断言保留,新增收紧句与
  DISTINCT 例外断言)。
- **冒烟发现收编:哨兵语义补全至列级**(046 冒烟「价格最高的3个商品」
  实测暴露:goods 表无 price 列,模型以 ORDER BY id 冒充价格排序并给出
  不实 summary)。规则 10 哨兵触发条件从「无支撑本问题的表」补全为两级:
  表级 + 列级(问句所需关键信息项不存在且无法由现有列合理推导,不得以
  其他列近似顶替);`_NO_DATA_ERROR` 文案同步泛化(api/web 兜底镜像同批),
  does-not-exist 自愈喂回引导泛化至表或列。
- 排序侧零改动:主键作 ORDER BY 排序键(tiebreaker/确定性排序)不在本次
  收敛范围——排序键不进结果集,无可读性代价。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `nl2sql-agent`:「SQL 生成质量规则」投影纪律升级——技术主键 SHALL 默认
  不进投影(用户点名除外)、业务编号列优先作展示标识、DISTINCT 去重查询
  SHALL NOT 追加投影外唯一列排序(PG 硬约束:ORDER BY 列必须在投影内);
  「表范围纪律与如实说明短路」哨兵语义补全两级(表级/列级,列级=问句口径
  所需列不存在且无法由现有列合理推导)、NO_DATA 文案泛化与喂回引导泛化
  (冒烟发现收编,见 What Changes 末条)。

## Non-goals

- 不动上游其他 prompt 资产(UNDERSTAND/SUMMARIZE/ReAct 内核独立资产均
  不引用规则 3/6)。
- 不改统计口径的主键用法:`COUNT(DISTINCT 实体主键)` 去重计数是聚合语义
  不是投影,不在收敛范围。
- 不改执行层/护栏(纯生成纪律,SQL 执行语义零变化)。
- 不改 018 既有结论:名称列必须在投影、投影完备、明细附标识列、非 DISTINCT
  top-N 的 tiebreaker 全部维持。
- evals gold 不随批重写:gold 题若存在依赖 id 投影的,实施期排查登记、按
  gold 规范单独立项处理(Execution Match 语义不变)。

## Impact

- `apps/agent/kongming_agent/graph/prompts.py`:SQL_GEN_SYSTEM 规则 3/6
  文本+反例区+规则 10 列级子句(例外清单登记)。
- `apps/agent/kongming_agent/graph/nodes.py`:`_NO_DATA_ERROR` 泛化+
  does-not-exist 喂回引导泛化(表或列)。
- `apps/api/kongming/chat/service.py` / `apps/web/src/components/assistant-card.tsx`:
  NO_DATA 兜底文案镜像同步(仅展示串,零契约/零映射变化)。
- `apps/agent/tests/`、`apps/api/tests/test_chat_query.py`:断言同步。
- `openspec/specs/nl2sql-agent/spec.md`:「SQL 生成质量规则」需求(归档期
  sync)。
- 历史成功 SQL 回写段(query_history few-shot)可能仍含旧形态带 id 投影——
  规则 10 已声明其「仅供参考口径」,生成纪律以系统提示为准,残余风险接受
  (design 备案)。
