## ADDED Requirements

### Requirement: 数据范围主体不可确定时 fail-closed

系统必须 (MUST) 在无法确定「当前用户能看哪些行」时拒绝执行 NL2SQL 生成的查询:
用户不存在或已软删、以及用户无任何启用角色(解析结果为空范围集),两种形态都必须抛
`DataScopeUnsupported`,禁止 (MUST NOT) 降级为「不过滤 + 告警」。该判定必须 (MUST) 位于
列探测与「主表不可定位 → 降级为不过滤」分支之前,与 CUSTOM 空集合同一顺序要求。
「表无过滤列时不改写」的降级先例只锚在 SQL 结构事实上,禁止 (MUST NOT) 被引申为
「身份解析不出时也可放行」:后者的「不过滤」等价于把未知折算成最大权限。
带 `user_id` 缺省的系统调用豁免(整体跳过 data_scope 改写)属另一条立约,不受本条约束。

#### Scenario: 用户查不到不得按最大范围执行

- GIVEN 请求携带的 `user_id` 在 `sys_user` 中不存在或已软删
- WHEN 执行需要数据范围改写的查询
- THEN 抛 `DataScopeUnsupported`(对外为既有 4xx 映射),且审计记录拒绝原因,不得返回未改写的原 SQL

#### Scenario: 仅有非启用角色不得按最大范围执行

- GIVEN 用户绑定的角色全部为 `status='DISABLED'`
- WHEN 执行需要数据范围改写的查询
- THEN 同样拒绝执行;禁用角色的效果只能是收紧可见面,禁止放宽为全表

### Requirement: 部门启停不参与行级可见性判定

行级数据范围必须 (MUST) 只由「角色声明的 `data_scope` + 用户所属部门 + 部门树结构」三件事决定;`sys_dept.status` 禁止 (MUST NOT) 作为任何一档范围(DEPT / DEPT_AND_BELOW / CUSTOM)的行过滤条件——它是组织呈现标志(部门树徽标),不是授权标志。三档口径必须 (MUST) 一致:禁止出现「递归展开剔除停用部门、而本人部门与自定义集合按字面生效」这类同标志分叉。若未来需要「停用即冻结可见性」的治理语义,必须 (MUST) 作为独立需求立项(含界面提示与审计说明),禁止 (MUST NOT) 藏在数据范围谓词里实现。

#### Scenario: 停用中间节点的子树仍在范围内

- GIVEN 部门 A 下有子部门 B、B 下有 C,且 B 与 C 的 `status='DISABLED'`
- WHEN 拥有「本部门及以下」范围、所属部门为 A 的用户解析可见部门集合
- THEN 递归结果仍包含 A、B、C,IN 列表不因部门启停状态而缩水

#### Scenario: 三档范围对部门状态同口径

- GIVEN 同一批部门分别处于 ACTIVE 与 DISABLED
- WHEN 依次以 DEPT、DEPT_AND_BELOW、CUSTOM 三档解析可见部门集合
- THEN 三者结果都只取决于用户所属部门与角色配置集合,与 `sys_dept.status` 无关

## MODIFIED Requirements

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
  13. 主体查不到(用户不存在/已软删)→ fail-closed 拒绝,不得返回未改写 SQL
  14. 仅有非启用角色 → fail-closed 拒绝(禁用角色只可收紧可见面)
  15. DEPT_AND_BELOW 递归含停用部门(部门启停不参与行过滤)
