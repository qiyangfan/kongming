# 086 提案：授权标志口径统一（角色启用状态全链同闸 + 部门启停退出行级可见性判定）

## Why

评审循环查出两条同一根因的缺陷：**同一个标志一半授权路径读、一半不读**。

1. **禁用角色会让行级可见面反向放宽（fail-open）**。`sys_role.status` 在
   `datascope/repository.py::user_role_scopes`（以及 `custom_dept_ids`）里是过滤条件，
   在功能权限解析 `auth/repository.py::SqlPermRepo.list_codes` 里**根本不在 SQL 里**
   （联查只到 `sys_user_role → sys_role_permission`）。于是把一个角色置为 `DISABLED` 后：
   用户照样过 `require_perm`，而数据范围解析拿到空集合，落进
   `applier.py` 的「用户无启用角色 → 降级为不过滤」分支——**治理动作的实际后果是看得见全表**。
   同一类降级分支还有一条：`user_dept_id` 查不到主体（用户不存在/已删除）时也是「不过滤 + warning」，
   等于把一个解析不出身份的 `user_id` 当超管使。
   另有四处 `bool_or(r.data_scope='ALL')` 判定（audit / feedback / toolcall / quality）同样不看 status：
   禁用一个 ALL 角色，这三条链路照旧按 ALL 放宽。
2. **部门启停在三种范围里语义不一致**。`dept_and_below` 的递归 CTE 过滤 `status='ACTIVE'`，
   而 `DEPT`（本人部门）与 `CUSTOM`（角色配置集合）按字面生效。后果是停用中间节点会让
   「本部门及以下」的负责人**凭空看不见整棵子树**，而同一个部门写进 CUSTOM 集合却照样可见——
   用户与管理员都无法从界面上解释这个差异。`sys_dept.status` 另有两处事实：
   它可由 `PUT /system/depts/{id}` 写入（rbac 规格的部门 CRUD 未列该字段，属实现超出规格），
   且停用**不**级联用户（`dept_exists`/`depts_exist` 也不过滤 status，允许把人或 CUSTOM 集合
   绑到停用部门）。

## What Changes

统一为一条原则：**一个标志要么在所有授权读取点参与判定，要么完全不参与；不允许部分路径读。**

1. **角色启用状态成为全链一致的授权闸**：`sys_role.status='ACTIVE'` 补进功能权限联查
   （`list_codes`）与四处 `data_scope='ALL'` 判定（audit / feedback / toolcall / quality），
   与 `datascope` 侧既有两闸同口径。此后「角色非启用」= 该角色既不授予权限点，
   也不授予任何数据范围。
2. **数据范围解析改为 fail-closed**：`applier` 的「主体查不到」「无启用角色」两条分支由
   「降级为不过滤 + warning」改为抛 `DataScopeUnsupported`。判据与 083 立的
   「CUSTOM 空集合不得漂成不过滤」同方向，且必须先于列探测与「主表不可定位 → 降级」。
   系统调用豁免（`user_id` 缺省整体跳过 data_scope 改写）是 `sql-execution` 明文立约，
   本批不动。
3. **部门启停退出行级可见性判定**：删掉递归 CTE 里的 `status='ACTIVE'`，三档范围
   （DEPT / DEPT_AND_BELOW / CUSTOM）一律只看「角色声明的 data_scope + 用户所属部门 + 树结构」。
   `sys_dept.status` 收敛为组织呈现标志（部门树徽标），并在规格里立 MUST NOT 防止再次被当作行过滤条件。
4. **规格与清单**：`rbac` 的权限点校验机制补启用状态闸与新场景；`datascope` 的 fail-closed
   范围扩到「主体不可确定」，用例清单增第 13/14/15 条（覆盖率 100% 的宪法要求随之扩表）。

## Non-goals

- **不给角色/部门加启停 UI 或端点**。角色面此前无任何写 `status` 的路径（仅 DDL 默认值 +
  测试旁路 SQL），本批只把语义定死；「角色启停」要成为产品能力是另一件事，需单独需求。
- **不删 `sys_role.status` / `sys_dept.status`，也不删 `DeptUpdateIn.status`**：列与字段保留
  （迁移 CHECK 与只读徽标依赖它们），本批改的是判定语义不是存储形状。
- **不改任何对外 HTTP 契约**：请求/响应字段、状态码集合与错误码词汇均不变；
  `DataScopeUnsupported` 的对外映射是既有的 4xx 分支，只是覆盖面变宽（此前它只在
  CUSTOM 空集合/未知值时出现）。
- **不做「停用部门即隐其历史数据」的实现**：若未来要这类治理语义，必须作为显性需求立项
  （含界面提示与审计说明），不能靠数据范围侧的隐式过滤偷偷实现。

## Impact

- **行为等价面**：所有角色均为 `ACTIVE` 时（种子与全部既有链路如此），权限判定与改写结果
  逐字节不变——新增的谓词恒真。四处 ALL 判定与 `dept_and_below` 同此。
- **变化面**：仅当存在 `DISABLED` 角色/部门时行为改变，方向是**收紧不是放宽**
  （禁用角色 → 403 而非全表；停用部门 → 子孙仍在范围内，可见面不再莫名缩水）。
  这是 fail-closed 铁律要求的方向。
- 测试：新增 5 条用例（`test_auth_me` 禁用角色权限消失 + 403、`test_datascope`
  主体查不到 / 仅禁用角色 / 停用部门在范围内、`test_role_status_gate` 四处 ALL 闸）；
  逐条做过「改回修前形态即红」实证。`make api-check` 全链（ruff/mypy/pyright/lint-imports/
  pytest+cov）复绿。
