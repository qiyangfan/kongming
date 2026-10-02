# 实施任务

清单冻结自 2026-10-02 `openspec validate --specs --strict` 输出(12 域);每项完成即复验该域 strict。

## 1. 结构债(ERROR 级)

- [x] 1.1 runtime-resilience:delta 形态转主规格规范形态(`# 能力域` + Purpose + Requirements,原文逐字保留),单域 strict 过。
- [x] 1.2 chat:口径依据持久化与回显 / 文档道回答的结构化来源两条补 Scenario 块(从正文派生),单域 strict 过。

## 2. 关键词债(WARNING 级,strict 下计失败)

- [x] 2.1 ai-eval 5 条;audit 1 条;datascope 6 条;datasource 1 条;eval 2 条;knowledge 1 条——注入 SHALL/MUST/SHALL NOT,语义不变。
- [x] 2.2 multitenant 4 条;nl2sql 6 条;web-admin 3 条——同上。
- [x] 2.3 rbac 15 条——同上(第 1/7 条已有关键词,不在债内,不动)。

## 3. 验证与收尾

- [x] 3.1 全量 `openspec validate --specs --strict` 45/45(0 失败),与冻结清单零遗漏、零新增。
- [x] 3.2 零代码/契约/迁移变更核对(git diff 仅 openspec/specs/ 与本 change 工件)。
- [x] 3.3 归档(`--skip-specs`,主规格已手工同步)+ HANDOFF 终态回写。
