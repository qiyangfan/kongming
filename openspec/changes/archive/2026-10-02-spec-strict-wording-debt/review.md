# 提案核验与实施验证

## 清单核对(冻结清单 × 实际修正)

冻结清单=2026-10-02 `openspec validate --specs --strict` 输出的 12 失败域;逐域修正后单域复验:

| 域 | 修正 | 单域 strict |
|---|---|---|
| runtime-resilience | delta 形态转主规格(Purpose 补写,需求/场景原文逐字保留) | valid |
| chat | 两条需求补 Scenario(口径依据×2、文档道来源×2,从 SHALL 正文派生) | valid |
| ai-eval | 5 条关键词注入 | valid |
| audit | 1 条(「必须」→MUST) | valid |
| datascope | 6 条 | valid |
| datasource | 1 条 | valid |
| eval | 2 条 | valid |
| knowledge | 1 条 | valid |
| multitenant | 4 条 | valid |
| nl2sql | 6 条 | valid |
| rbac | 15 条(第 1/7 条既有关键词不动) | valid |
| web-admin | 3 条 | valid |

## 全量验证(2026-10-02)

- `openspec validate --specs --strict`:**45 passed, 0 failed(45 items)**,自 33/45 清零;
  与冻结清单对照零遗漏、零新增失败项。残余 INFO 级「超长文本」提示(web-nav 新需求、
  workflow-orchestration、audit/datasource/knowledge/chat/eval 数条)不影响判定,按冻结
  裁决不处理。
- 零行为变更核对:git diff 仅 `openspec/specs/` 12 个 spec.md 与本 change 工件;无代码、
  无契约、无迁移、无前端变更,api/agent/web 门禁不触发。
- 场景派生自查:chat 新增 4 个 Scenario 的每条 THEN 均可溯源到对应需求的既有 SHALL
  句(口径依据:三路径同源/存量回空不反查;文档道:正文存活引用一一对应/SQL 道恒空)。

## 终态

- 主规格 strict 门禁自此为全绿基线;后续新 change 的 delta 自身须过 `--strict`(既有纪律)。
- 「部分通过常态化」的复述负担(每轮 HANDOFF 记 33/45)终止。
