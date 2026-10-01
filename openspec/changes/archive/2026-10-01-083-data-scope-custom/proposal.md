# 083 提案：data_scope=CUSTOM 落地（角色自定义部门集合）

## Why

CUSTOM 是「已登记未实现」的能力，而且它已经变成一个**用户可踩中的缺陷**，不只是纸面欠账：

- 枚举自 baseline 就在：`apps/api/alembic/versions/0001_baseline.py:75` 的 `data_scope` CHECK 含 CUSTOM；`openspec/specs/datascope/spec.md:7` 把五档枚举写成契约，`:64-77` 记「未实现前 fail-closed，实现后该拒绝自动消失」。
- 写侧一直**允许**选它：`apps/api/kongming/system/router.py:58` 的 `DataScope` Literal、`RoleCreateIn/RoleUpdateIn` 都接受 CUSTOM。
- 读侧执行时**必拒**：`datascope/service.py` 对 CUSTOM 判 `UNSUPPORTED` → `applier.py` 抛 `DataScopeUnsupported`。
- 前端把它做成可选项：`apps/web/src/pages/admin/RolesPage.tsx:49` 的 `{ value: "CUSTOM", label: "自定义(暂未开放)" }` 是可选中的下拉项。管理员选它 → 保存成功 → 该角色用户每次问数都被拒。这就是「可感知的坏 affordance」：界面给了路，路尽头是墙。

两条出路：收窄对外契约（从枚举与下拉里去掉 CUSTOM），或把它实现完。选后者——
无明确需求不得改动对外契约（本仓纪律），且 CUSTOM 是企业 RBAC 数据范围的标配档，
缺它反而是能力短板；实现后那条 fail-closed 铁律自然收窄为「集合未配置时仍拒绝」。

## What Changes

1. **存储底座**（迁移 `0035_sys_role_dept.py`）：新增关联表 `public.sys_role_dept(role_id, dept_id)`，复合主键 + 两侧外键 + `dept_id` 索引；纯加法可回滚。不用 `sys_role` 上的 JSON 列——权威库里部门集合是关系不是文档，FK 完整性、按部门反查角色、集合去重都靠关系约束兑现（详见 design D1）。
2. **写侧契约**（纯增字段）：`RoleCreateIn`/`RoleUpdateIn` 新增可选 `data_scope_dept_ids`（`None`=键缺失，与显式空数组分开下传）；`RoleOut` 新增同名字段，非 CUSTOM 恒空。
3. **写侧校验**：CUSTOM 必须配非空且真实存在的部门集合，否则 400；非 CUSTOM 却带集合 → 显式拒绝（不静默忽略，杜绝「配了部门却不生效」的隐性状态）；整包替换语义与既有权限绑定一致；被引用的部门因 FK 不可删。
4. **读侧执行**：`datascope/repository.py` 取「该用户启用角色中 CUSTOM 的部门并集」；`applier.py` 把 CUSTOM 纳入部门系探测与同一 `DEPT_IN` 改写；**CUSTOM 不递归子部门**（递归是 DEPT_AND_BELOW 的职责）；空集合仍 fail-closed，且判定**提到列探测与「主表不可定位→降级」之前**，堵掉一条潜在 fail-open 路径。
5. **前端**：CUSTOM 去掉「(暂未开放)」；选中时在同一表单内出现部门多选（新增 `lib/dept-tree.ts` 纯函数 + `components/check-list.tsx`，数据源复用 `DeptsPage` 的部门端点）；非 CUSTOM 不显示也不发该字段。
6. **随行修复两条既有缺陷**（不修则门禁无法复现绿灯，非本变更功能）：
   - `tests/test_feedback_promotion.py` 的迁移往返用例 `downgrade("0033")` 后只 `upgrade("0034")`，不回 head：在 0034 即 head 时无害，任何新迁移加入后，其后每个用例的 `TRUNCATE` 都会撞上不存在的表（本次实测 518 个错误的唯一成因）。
   - `tests/test_billing.py::test_monthly_invoice_idempotent` 的「上月」取自库时钟（UTC），而 `BillingService._prev_month()` 用固定 +8 口径——每月 1 日 00:00~08:00（北京时间）两者跨月错位，用量写进产品不出账的月份，断言必红。改为与产品同一时源。

## Non-goals

- **不做部门树的权限继承计算**（如「父部门集合自动展开子树」）：那是 DEPT_AND_BELOW 的语义，混进 CUSTOM 会让两档失去可解释性。
- **不做「按部门反查角色」的新管理界面**：FK 与索引已备好该查询，无消费场景不先建页面。
- **不改多角色取最宽松的裁决**（rbac spec）：本轮只把 CUSTOM 档的读数从「必拒」变成「按集合过滤」。
- **不动 ALL/DEPT/DEPT_AND_BELOW/SELF 的既有改写与降级策略**（回归零变化）。
- **不引入行级策略（RLS）或视图层方案**：AST 改写是既有契约（spec「改写后的 SQL 安全性」）。

## Impact

- **后端**：`alembic/versions/0035_sys_role_dept.py`（新）、`datascope/{applier,models,repository,service}.py`、`system/{models,ports,repository,router,service}.py`。
- **前端**：`pages/admin/RolesPage.tsx`、`pages/admin/DeptsPage.tsx`、`components/check-list.tsx`（新）、`lib/dept-tree.ts` + `lib/role-scope.ts`（新，纯函数便于单测）。
- **契约**：`contracts/openapi.json` 与 `apps/web/src/lib/api-types.gen.ts` 随 `make contracts` 再生成（纯增字段）。
- **测试**：`tests/test_datascope.py`、`tests/test_system_admin.py`、`tests/test_internal_execute.py`（原断言 CUSTOM 被拒的用例语义更新）、`tests/conftest.py`（TRUNCATE 清单）；spec 用例清单第 9 条随之改写。
- **不改**：既有四档行为、对外字段名与状态码语义（除 CUSTOM 由「必拒」变「可用」这一登记过的欠账兑现）。
