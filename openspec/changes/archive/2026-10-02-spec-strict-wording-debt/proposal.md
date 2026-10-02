# 提案:spec-strict-wording-debt(spec strict 存量债清偿包)

## Why

`openspec validate --specs --strict` 长期为 33/45:12 个既有域的失败全部是**措辞/结构债**,
不是行为分歧——Requirement 正文缺 RFC 2119 关键词(WARNING 级 44 条)、chat 两条需求
缺 Scenario 块(ERROR 级)、runtime-resilience 的主规格文件当年以 **delta 形态**(074 归档件
的 `## ADDED Requirements` 原文)直接躺进了主规格目录,缺 `## Purpose` 与规范头(ERROR 级)。
门禁长期红导致「部分通过」被常态化,HANDOFF 每轮都要复述「12 项失败是既有债、不算本轮」。
2026-10-02 owner 裁决:遗留但冻结,排期为 CardTitle 归档后第一个 change,债项清单以当日
validate 输出为准冻结,不再扩大评审面。CardTitle 已于同日归档,本件即该排期件。

## What Changes

12 个域、49 处修正(44 处关键词升格 + 4 个新增 Scenario + 1 次结构规范化),**零行为变更**(只改 spec 文本的规范措辞与结构形态):

| 域 | 修正 | 处数 |
|---|---|---|
| ai-eval | 5 条 Requirement 正文注入 SHALL/SHALL NOT | 5 |
| audit | 「必须」升格 MUST(查询审计落库) | 1 |
| chat | 口径依据/文档道来源两条需求各补 2 个 Scenario,共 4 块(从既有 SHALL 正文严格派生,不添新行为) | 4 块 |
| datascope | 6 条注入 SHALL/MUST/SHALL NOT | 6 |
| datasource | 1 条(RAG datasource 维度)注入 | 1 |
| eval | 2 条注入(评估匹配规则/gold_sql 编写规范) | 2 |
| knowledge | 1 条(不变式约束)注入 | 1 |
| multitenant | 4 条注入 | 4 |
| nl2sql | 6 条注入 | 6 |
| rbac | 15 条注入(第 1/7 条已有关键词,不在债内) | 15 |
| runtime-resilience | **delta 形态转主规格形态**:`# runtime-resilience 能力域` + `## Purpose` + `## Requirements`,需求与场景原文逐字保留 | 结构 |
| web-admin | 3 条注入 | 3 |

超长文本(`>500 characters`)在多域为 INFO 级提示,不影响 strict 判定,本件不一并处理
(扩评审面违冻结裁决;workflow-orchestration 与 web-nav 同有此 INFO 且通过)。

## Non-goals

- **零行为变更**:不改任何 MUST/SHALL 的语义指向,不新增/删除需求,不改任何 Scenario
  的既有断言;chat 新增的两个 Scenario 块逐句从对应需求既有 SHALL 正文派生。
- 不处理 INFO 级「需求文本超长」提示(拆分需求属评审面扩张,冻结清单不含)。
- 不改代码、不改契约、不动迁移——纯 spec 文本治理。
- 不在本件内顺带评审任何域的行为口径;发现的行为疑点走正常变更,不混入本件。

## Capabilities

### New / Modified Capabilities

无 delta 工件:修正直接以**主规格手工同步**落地(本仓既有纪律「specs 以 apply 期手工同步
为終态」,归档走 `--skip-specs`);理由是全部修正是措辞升格与结构形态,不构成行为变更,
为 12 个域各写一套 MODIFIED delta 会使同一文本双份维护、徒增漂移面。

## Impact

- `openspec/specs/` 下 12 个域的 spec.md(ai-eval/audit/chat/datascope/datasource/eval/
  knowledge/multitenant/nl2sql/rbac/runtime-resilience/web-admin)。
- 门禁:`openspec validate --specs --strict` 由 33/45 → **45/45**;`--changes --strict`
  归档后活动件为零;代码门禁不触发(零代码变更)。
