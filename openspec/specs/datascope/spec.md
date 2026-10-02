# datascope 能力域

## Purpose

按角色 data_scope 自动改写 NL2SQL 生成的 SQL,加行级过滤。
受影响权限层级: 行级(基于 SQL AST 的 WHERE 注入改写)。
data_scope 枚举: ALL / CUSTOM / DEPT / DEPT_AND_BELOW / SELF。
本域是最深、最易出 bug 的部分,相关规则测试覆盖率必须 100%(宪法 Article III)。

## Requirements

### Requirement: ALL 不改写

data_scope=ALL(全部)时 SHALL NOT 改写 SQL,原样执行。

#### Scenario: 全部权限 SQL 原样执行
- GIVEN 当前用户角色 data_scope=ALL
- WHEN 生成 SQL「SELECT * FROM customer」
- THEN 不改写,原样经防火墙校验并执行,返回所有行

### Requirement: SELF 加创建者过滤

data_scope=SELF(仅本人)时 SHALL 加 WHERE 创建者列过滤;业务表无该列时 SHALL 安全降级(不改写 + 审计告警)。

#### Scenario: 仅本人加创建者过滤
- GIVEN data_scope=SELF,当前用户 id=U
- AND 业务表有创建者列
- WHEN 生成「SELECT * FROM customer」
- THEN 改写为最外层查询 WHERE 注入创建者=U 谓词(列限定主表),只返回本人创建的行

#### Scenario: 表无创建者列安全降级
- GIVEN data_scope=SELF,业务表无创建者列
- WHEN 处理该 SQL
- THEN 不改写(避免加不存在的列导致语法错),审计告警「data_scope=SELF 但表无创建者列,降级为不过滤」,原样执行

安全权衡:业务表多为只读参考数据无创建者列,强加过滤会破坏可用性(沿用前系统裁定)。

### Requirement: DEPT 加部门过滤

data_scope=DEPT(本部门)时 SHALL 加 WHERE 部门列过滤;表无部门列时 SHALL 安全降级(同 SELF 降级策略)。

#### Scenario: 本部门加部门过滤
- GIVEN data_scope=DEPT,用户部门 D
- AND 业务表有部门列
- WHEN 生成「SELECT * FROM customer」
- THEN 改写为最外层查询 WHERE 注入部门=D 谓词(列限定主表),只返回本部门行

### Requirement: DEPT_AND_BELOW 递归加 IN 过滤

data_scope=DEPT_AND_BELOW(本部门及以下)SHALL 递归查子部门(数据库递归 CTE),并加 部门 IN (...) 过滤。

#### Scenario: 递归查子部门
- GIVEN data_scope=DEPT_AND_BELOW,用户部门 D
- AND 部门层级 D → D1 → D2(D 是 D1 的父,D1 是 D2 的父)
- WHEN 生成「SELECT * FROM customer」
- THEN 先递归查出 [D, D1, D2]
- AND 改写为 WHERE 部门 IN (D, D1, D2),返回这三个部门的所有行

#### Scenario: 部门无子级
- GIVEN 部门 D 无子部门
- WHEN 处理
- THEN IN 列表仅含 D

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

### Requirement: 改写后的 SQL 安全性(纵深防御)

改写 SHALL 基于 SQL AST(非字符串拼接);改写后的 SQL MUST 再过一次防火墙校验(防改写引入非法语句);复杂 SQL 在最外层查询注入 WHERE 谓词 SHALL NOT 破坏原逻辑(过滤列属于 FROM 主表,与原查询投影解耦)。

#### Scenario: 改写后再过防火墙
- GIVEN 改写了 SQL
- WHEN 改写后传入执行
- THEN 再过一次防火墙校验,失败则拒绝

#### Scenario: 参数值防注入
- GIVEN 构造的过滤值(用户 id/部门 id)
- WHEN 改写拼接过滤条件
- THEN 在 AST 表达式层面以绑定参数注入,非字符串拼接,无注入风险

#### Scenario: 复杂 SQL 改写正确
- GIVEN 生成「SELECT c.name, COUNT(r.id) FROM customer c JOIN rental r ON ... GROUP BY c.name」
- WHEN data_scope=DEPT 处理
- THEN 谓词 AND 进最外层 WHERE(限定主表别名,聚合前过滤),不破坏原 JOIN/GROUP BY 逻辑,改写后语法正确可执行
- AND 原查询未投影过滤列时(如 SELECT mark FROM goods)改写仍正确执行

### Requirement: data_scope 仅作用于 NL2SQL 查询链路

data_scope SHALL 仅作用于 NL2SQL 查询执行链路;管理接口 SHALL NOT 套此机制,其权限由权限点控制,两者正交。

#### Scenario: 管理接口不走 data_scope
- GIVEN 管理员调用用户管理列表
- WHEN 查询
- THEN 不经 data_scope 改写(管理接口的可见性由接口自身显式按数据范围收敛,如审计/反馈域的行级可见性条款)

#### Scenario: NL2SQL 链路强制套用
- GIVEN NL2SQL 生成 SQL 经控制面执行
- WHEN 执行前
- THEN 强制按当前用户 data_scope 改写,无法绕过

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
