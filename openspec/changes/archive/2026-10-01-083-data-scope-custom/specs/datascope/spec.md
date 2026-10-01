## ADDED Requirements

### Requirement: CUSTOM 按角色自定义部门集合过滤

`data_scope=CUSTOM` 时,系统必须 (MUST) 取该用户全部**启用**角色所配置的自定义部门集合的并集,并按 `dept_id IN (集合)` 改写查询(与 DEPT 系共用同一 IN 语义、同一列探测与安全降级策略)。集合必须 (MUST) 按字面生效:不递归子部门(递归属 DEPT_AND_BELOW 语义),不自动包含用户自身部门(除非该部门被显式配置)。

#### Scenario: CUSTOM 配置两个部门

- GIVEN 某角色 data_scope=CUSTOM 且自定义部门集为 [D1, D2],用户启用该角色
- WHEN 生成「SELECT * FROM customer」
- THEN 改写为最外层 WHERE dept_id IN (D1, D2),仅返回这两个部门的行

#### Scenario: CUSTOM 不递归子部门

- GIVEN CUSTOM 集合含父部门 D1,而 D1 存在子部门 D11
- WHEN 处理该用户的查询
- THEN 结果只含 D1 的行,不含 D11 的行

#### Scenario: 多启用角色取并集

- GIVEN 同一用户启用两个 CUSTOM 角色,部门集分别为 [D1] 与 [D2]
- WHEN 处理查询
- THEN IN 列表为 {D1, D2} 并集,不含未启用角色的配置

### Requirement: CUSTOM 集合的写入前置校验

角色写侧必须 (MUST) 校验档位与部门集合的搭配:`data_scope=CUSTOM` 且集合缺失或为空时拒绝保存(400);集合含不存在的部门时拒绝(400,复用既有 UnknownDeptId 语义);`data_scope≠CUSTOM` 却提交非空集合时拒绝(禁止静默忽略,以免形成「界面已配置、执行不生效」的第二套事实)。集合以整包替换语义落库(与权限绑定一致),并被库层外键约束保护:被角色引用的部门不得删除。

#### Scenario: 选 CUSTOM 未配部门被拒

- GIVEN 管理员提交 data_scope=CUSTOM 且未提供 data_scope_dept_ids(或空数组)
- WHEN 创建或更新角色
- THEN 400 拒绝,角色不落库为 CUSTOM

#### Scenario: 非 CUSTOM 携带部门集合被拒

- GIVEN 管理员提交 data_scope=DEPT 且携带非空 data_scope_dept_ids
- WHEN 保存角色
- THEN 400 拒绝,不做「忽略该字段」的静默降级

#### Scenario: 被引用的部门不可删除

- GIVEN 某部门已被任一角色的 CUSTOM 集合引用
- WHEN 删除该部门
- THEN 被拒绝(FK 保护),不产生悬空授权

### Requirement: 契约纯增字段向后兼容

`RoleOut` 必须 (MUST) 新增 `data_scope_dept_ids`(非 CUSTOM 恒为空数组),`RoleCreateIn`/`RoleUpdateIn` 必须 (MUST) 新增可选 `data_scope_dept_ids`;字段缺失与显式空数组必须 (MUST) 区分处理。既有请求与响应字段名、状态码语义不得改变,旧客户端可忽略新增字段。

#### Scenario: 既有客户端不传新字段仍可管理非 CUSTOM 角色

- GIVEN 请求体不含 data_scope_dept_ids
- WHEN 创建或更新 data_scope=SELF 的角色
- THEN 行为与本变更前一致,响应中该字段为空数组

## MODIFIED Requirements

### Requirement: CUSTOM 不支持时 fail-closed

(本需求在 CUSTOM 实现后收窄为「集合未配置时仍 fail-closed」;标题保留以维持规格追踪连续性。)

`data_scope=CUSTOM` 但有效部门集合为空(未配置、历史数据或旁路写入)时,系统必须 (MUST) 显式拒绝(DATA_SCOPE_UNSUPPORTED),禁止 (MUST NOT) 降级为不过滤,亦禁止 (MUST NOT) 改写成恒假条件把「配置缺失」伪装成「查无数据」。该判定必须 (MUST) 发生在列探测与「主表不可定位 → 降级为不过滤」分支之前,杜绝 fail-open。未知 data_scope 取值同样 fail-closed。

#### Scenario: 遇到 CUSTOM 抛异常拒绝

- GIVEN 某角色 data_scope=CUSTOM 且未配置任何部门
- WHEN 处理该角色的查询
- THEN 显式拒绝(DATA_SCOPE_UNSUPPORTED),不执行查询

#### Scenario: 主表不可定位时不得漂成不过滤

- GIVEN 某用户有效 scope=CUSTOM 且集合为空,SQL 为 UNION/子查询 FROM(取不到主表)
- WHEN 处理该 SQL
- THEN 仍按空集合 fail-closed 抛异常,不得落入「降级为不过滤」分支返回全量数据

### Requirement: 单元测试覆盖(宪法 Article III 强制)

data_scope 改写规则的测试覆盖率必须 (MUST) 达 100%,且必须 (MUST) 覆盖下列用例清单并全部通过。

#### Scenario: 测试用例清单
- GIVEN data_scope 改写规则的测试套件
- THEN 必须覆盖全部用例并全部通过:
  1. ALL,不改写
  2. DEPT,表有部门列,正确改写
  3. DEPT,表无部门列,安全降级
  4. DEPT_AND_BELOW,递归查子部门,IN 列表正确
  5. DEPT_AND_BELOW,部门无子,IN 列表只含自己
  6. SELF,表有创建者列,正确改写
  7. SELF,表无创建者列,安全降级
  8. 复杂 SQL(JOIN+子查询),改写后语法正确
  9. CUSTOM,按角色部门集合 IN 过滤(多角色并集、不递归子部门)
  10. CUSTOM,集合为空显式拒绝(fail-closed),且判定先于「主表不可定位→降级」
  11. 改写后再过防火墙,防御性校验生效
  12. 写侧搭配校验(CUSTOM 缺集合拒 / 非 CUSTOM 带集合拒 / 部门不存在拒 / 被引用部门不可删)
