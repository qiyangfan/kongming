# 086 实施任务：授权标志口径统一

## 1. 角色启用状态全链同闸

- [x] 1.1 `auth/repository.py::SqlPermRepo.list_codes` 联查补 `JOIN sys_role r ... AND r.status='ACTIVE'`（注释写明它此前缺什么、为何不能一半读一半不读）
- [x] 1.2 `audit/repository.py::user_has_scope_all` 补同闸
- [x] 1.3 `feedback/repository.py::user_has_scope_all` 补同闸（docstring 记闸的来由）
- [x] 1.4 `toolcall/repository.py::user_has_all_scope` 补同闸（与 014 同口径注释互指）
- [x] 1.4b `quality/repository.py::user_has_scope_all` 补同闸——R2 复核抓出的第 4 处：它直接决定跨租户全量可见，漏闸即本变更铁律自身的破口
- [x] 1.5 四处判定不抽公共件：模块依赖受 `lint-imports` 约束，跨模块抽服务属结构重构（design D4）；以「同一份数据同时喂四处」的横向用例钉口径

## 2. 数据范围主体不可确定时 fail-closed

- [x] 2.1 `datascope/applier.py`「用户不存在或已删除」分支由「不过滤 + warning」改为抛 `DataScopeUnsupported`
- [x] 2.2 同文件「用户无启用角色」分支同上改造
- [x] 2.3 两条判定仍位于列探测与「主表不可定位 → 降级」之前（沿用 083 的顺序要求）
- [x] 2.4 `datascope/models.py::DataScopeUnsupported` docstring 扩为「无法确定可见面的统称」并列出四种触发形态
- [x] 2.5 系统调用豁免（`user_id` 缺省整体跳过）保持不动（`sql-execution` 立约，design D5）

## 3. 部门启停退出行级可见性判定

- [x] 3.1 `datascope/repository.py::dept_and_below` 递归 CTE 删除两处 `status='ACTIVE'`（锚点与递归步各一处）
- [x] 3.2 函数 docstring 记明「部门启停是组织呈现标志、不参与行过滤」及三档统一后的理由（design D3）
- [x] 3.3 确认 `DEPT` / `CUSTOM` 两档本就不读 status（无需改动，仅需同口径文档化）；`dept_exists` / `depts_exist` 保持不过滤 status

## 4. 用例（每条都做过「改回修前形态即红」实证）

- [x] 4.1 `tests/test_auth_me.py::test_me_perms_exclude_disabled_role`：禁用角色后 `/auth/me` 的 `perm_codes` 清空 + `GET /system/users` 403
- [x] 4.2 `tests/test_datascope.py::test_apply_unknown_subject_fail_closed`（清单 13）
- [x] 4.3 `tests/test_datascope.py::test_apply_only_disabled_role_fail_closed`（清单 14）
- [x] 4.4 `tests/test_datascope.py::test_dept_and_below_includes_disabled_dept`（清单 15）
- [x] 4.5 `tests/test_role_status_gate.py`：同一 ACTIVE/DISABLED 用户同时喂 audit / feedback / toolcall / quality 四处 ALL 判定
- [x] 4.6 `_mk_role` 测试助手加 `status` 形参（缺省 `ACTIVE`，既有调用零改动）

## 5. 规格与门禁

- [x] 5.1 `rbac` 主规格「权限点校验机制」MODIFIED（联查按启用角色）+ 新场景
- [x] 5.2 `datascope` 主规格 ADDED 两条（主体不可确定 fail-closed / 部门启停不参与行过滤）+ 用例清单扩至 15 条
- [x] 5.3 `openspec validate --changes --strict` 通过；`--specs --strict` 保持 33 passed / 12 failed——那 12 项是全仓既有的中文 RFC2119 关键词欠账（校验器只认英文 SHALL/MUST），本批未新增失败项：改前改后同为 33/12，`rbac`/`datascope` 两条被改的 Requirement 报的也是同一类 WARNING
- [x] 5.4 `make api-check` 全链绿（ruff format / ruff check / mypy / pyright / lint-imports / pytest+cov 覆盖率闸门）
- [x] 5.5 HANDOFF 挂账节两条「待 owner 裁决」项改为终态（已裁决并落地），并登记未做的相邻项
