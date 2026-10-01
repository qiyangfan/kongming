# 083 实施任务：data_scope=CUSTOM

## 1. 测试先行（宪法 Article III：datascope 域 100% 覆盖；权限相关先红）

- [x] 1.1 失败测试：CUSTOM 配 [D1,D2] → 改写为 `dept_id IN (D1,D2)`；不递归子部门；多启用角色取并集
- [x] 1.2 失败测试：CUSTOM 集合为空 → `DataScopeUnsupported`；且 SQL 为 UNION/子查询 FROM（主表不可定位）时仍拒绝，不得漂成「降级不过滤」
- [x] 1.3 失败测试：写侧 CUSTOM 缺集合 / 空数组 / 含不存在部门 / 非 CUSTOM 携带集合 → 全部 400 且不落库
- [x] 1.4 失败测试：角色读写 roundtrip 保 `data_scope_dept_ids`；非 CUSTOM 恒空；请求体不含该字段的旧客户端行为不变
- [x] 1.5 失败测试：被 CUSTOM 引用的部门删除被拒（FK）
- [x] 1.6 更新既有语义：`tests/test_internal_execute.py` 原「CUSTOM 必被拒」用例改为「按集合过滤生效」

## 2. 实现

- [x] 2.1 迁移 `0035_sys_role_dept.py`：关联表（复合主键 + 两侧 FK + dept_id 索引），纯加法可 downgrade
- [x] 2.2 `datascope/repository.py`：启用角色 CUSTOM 部门集合并集查询
- [x] 2.3 `datascope/applier.py`：CUSTOM 纳入部门系探测与同一 IN 改写；`unsupported_reason` 判定提到列探测之前（fail-open 路径封堵）
- [x] 2.4 `datascope/service.py` / `models.py`：CUSTOM 走 DEPT_IN；空集合 fail-closed；注释口径同步（`PERMISSIVENESS` 说明宽严由集合决定）
- [x] 2.5 `system/{models,ports,repository,service,router}.py`：契约纯增 `data_scope_dept_ids`（None/[] 分开）、搭配校验、整包替换、新异常 `CustomDeptScopeInvalid` → 400

## 3. 前端

- [x] 3.1 CUSTOM 选项去掉「(暂未开放)」
- [x] 3.2 `lib/dept-tree.ts`（扁平→树）与 `lib/role-scope.ts`（档位↔字段可见性/载荷规则）拆成无框架纯函数并带 vitest
- [x] 3.3 `components/check-list.tsx` 部门多选（复用 DeptsPage 的部门数据源；非 CUSTOM 整块不渲染且不发字段）；`RolesPage.tsx` 接入
- [x] 3.4 窄屏 375 档部门多选不产生横向溢出

## 4. 随行修复（不修则门禁不可复现，见 design D7；均只动测试，不改产品行为）

- [x] 4.1 `tests/test_feedback_promotion.py`：迁移往返用例 `downgrade("0033")` 后回 `head`（原钉死 "0034" 会把其后每个用例的 TRUNCATE 撞上不存在的表）
- [x] 4.2 `tests/test_billing.py`：出账用例「上月」改用产品同一时源 `BillingService._prev_month()`（原用库时钟 UTC，每月 1 日 00:00~08:00 北京时间跨月错位）

## 5. 收尾

- [x] 5.1 `make contracts` 再生成 `contracts/openapi.json` 与 `apps/web/src/lib/api-types.gen.ts`
- [x] 5.2 真库跑迁移：`uv run alembic upgrade head`（0034 → 0035 实录）
- [x] 5.3 api 侧静态门禁：ruff format --check / ruff check / mypy / pyright / lint-imports 全绿
- [x] 5.4 `uv run pytest -q` 全绿（1064 passed / 6 skipped，无 error；修复 4.1 前的 518 个 error 归零）
- [x] 5.5 `make check` 全绿：api（ruff format/check + mypy + pyright + lint-imports 22 契约 KEPT + pytest --cov）→ agent → liannu（89%）→ web（tsc/eslint/stylelint/prettier）逐段绿；vitest 66/66。注：`contracts-check` 的 `git diff --exit-code` 在「产物未提交」状态必然报差（该闸按 CI 提交后语义设计），本地已用「重新导出 + 重新生成 TS 类型后 web-check 仍绿」证明产物与代码一致
- [x] 5.6 UI 实机走查**已完成（2026-10-01，真浏览器 + 真 api + 真库）**：环境为 api 起 8001（指向 `kongming_test`，先 `alembic upgrade head` 到 0035）、vite 5178 代理过去、一次性管理员账号（不使用任何真实凭证）、种子部门 3 个（含一个 31 字超长名）。逐条结论见下节 §6。
- [x] 5.7 `openspec validate 2026-10-01-083-data-scope-custom --type change --strict` 通过（MODIFIED 保留原场景名「遇到 CUSTOM 抛异常拒绝」「测试用例清单」）；归档 sync 入主规格。注：`openspec validate --specs --strict` 存量 12 项失败与本批无关（全仓 spec 普遍用中文「必须」而未写 (MUST)，另 chat spec 有 2 条需求缺 Scenario）

## 6. 走查结论（2026-10-01，浏览器实机）

- **功能面**：① 选 CUSTOM → 部门多选面板出现，并带口径提示「只放行所选部门的数据，不含其下级部门（要含下级请选「本部门及以下」）」；② 空集合保存被前端 `deptScopeError` 拦下，以 `role=alert` 呈现「自定义范围需至少选择一个部门」且对话框不关；③ 勾一个部门保存 → 请求体 `data_scope_dept_ids:[<uuid>]`，`sys_role_dept` 落库一行；④ 重开编辑 → 该部门回显已勾选，列表行显示「自定义部门 · 1 部门」；⑤ 改回「仅本人」→ 面板消失、**请求体不带该字段**，且库里集合行归零（不残留失效授权，据此修正 design D5 初稿「None=不动」的说法）；⑥ 下拉项文案已是「自定义部门」，无「暂未开放」。
- **闸门 B 七类**：01✓ 02✓ 03✓（交互走 ui/Checkbox 原生语义）04✓（超长名截断 + title 回读；375 档 `documentElement.scrollWidth == 375` 无横向溢出；空态走 EmptyState）05✓ 06✓（与「角色权限绑定」面板同一 `CheckList` 单源）07✓（组件内无裸色值，全走 token）。
- **走查抓出并已修的两条**（均在新组件 `components/check-list.tsx`）：① 行高实测 20px，低于本仓 B1.2 触控热区 ≥44 → 改 `min-h-11`，实测 44px；② 超长部门名 `truncate` 后无 `title`，全名不可回读（04 类硬性项）→ label 挂 `title`，部门候选传原始名（不带层级缩进的全角空格）。该组件同时服务既有权限面板，32 行一并到 44px 并获得 title，实测无回归。修后 `make web-check` 全绿、vitest 66/66。
- **一条既有观察（本批不动）**：暗色下 `border-input` 与弹层底色对比度实测 1.49:1，低于非文本控件 3:1 参考线；它是全仓共享 token（所有输入框/复选框同源），改它等于改全站视觉基线，另案处理。
