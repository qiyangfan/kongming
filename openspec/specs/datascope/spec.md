# datascope 能力域

## Purpose

按角色 data_scope 自动改写 NL2SQL 生成的 SQL,加行级过滤。
受影响权限层级: 行级(基于 SQL AST 的 WHERE 注入改写)。
data_scope 枚举: ALL / CUSTOM / DEPT / DEPT_AND_BELOW / SELF。
本域是最深、最易出 bug 的部分,相关规则测试覆盖率必须 100%(宪法 Article III)。

## Requirements

### Requirement: ALL 不改写

data_scope=ALL(全部)时不改写 SQL,原样执行。

#### Scenario: 全部权限 SQL 原样执行
- GIVEN 当前用户角色 data_scope=ALL
- WHEN 生成 SQL「SELECT * FROM customer」
- THEN 不改写,原样经防火墙校验并执行,返回所有行

### Requirement: SELF 加创建者过滤

data_scope=SELF(仅本人)时加 WHERE 创建者列过滤;业务表无该列时安全降级(不改写 + 审计告警)。

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

data_scope=DEPT(本部门)时加 WHERE 部门列过滤;表无部门列时安全降级(同 SELF 降级策略)。

#### Scenario: 本部门加部门过滤
- GIVEN data_scope=DEPT,用户部门 D
- AND 业务表有部门列
- WHEN 生成「SELECT * FROM customer」
- THEN 改写为最外层查询 WHERE 注入部门=D 谓词(列限定主表),只返回本部门行

### Requirement: DEPT_AND_BELOW 递归加 IN 过滤

data_scope=DEPT_AND_BELOW(本部门及以下)递归查子部门(数据库递归 CTE),加 部门 IN (...) 过滤。

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

data_scope=CUSTOM(自定义)未实现前,遇到时显式拒绝(fail-closed),不降级。

#### Scenario: 遇到 CUSTOM 抛异常拒绝
- GIVEN 某角色 data_scope=CUSTOM
- WHEN 处理该角色的查询
- THEN 显式拒绝(DATA_SCOPE_UNSUPPORTED),不执行查询

设计决策(沿用前系统架构师裁定,fail-closed 铁律):
- 禁止 fail-open:降级到 ALL 是权限放大,严重安全缺陷
- 禁止静默降级到 SELF:看似安全,但运维以为生效实际不符,难排查
- 显式失败最安全:异常让问题立刻可见
- 常态不触发:种子数据不建 CUSTOM 角色,此为防御性设计;实现自定义范围后该拒绝自动消失

### Requirement: 改写后的 SQL 安全性(纵深防御)

改写基于 SQL AST(非字符串拼接);改写后的 SQL 必须再过一次防火墙校验(防改写引入非法语句);复杂 SQL 在最外层查询注入 WHERE 谓词不破坏原逻辑(过滤列属于 FROM 主表,与原查询投影解耦)。

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

data_scope 仅作用于 NL2SQL 查询执行链路;管理接口不套此机制,其权限由权限点控制,两者正交。

#### Scenario: 管理接口不走 data_scope
- GIVEN 管理员调用用户管理列表
- WHEN 查询
- THEN 不经 data_scope 改写(管理接口的可见性由接口自身显式按数据范围收敛,如审计/反馈域的行级可见性条款)

#### Scenario: NL2SQL 链路强制套用
- GIVEN NL2SQL 生成 SQL 经控制面执行
- WHEN 执行前
- THEN 强制按当前用户 data_scope 改写,无法绕过

### Requirement: 单元测试覆盖(宪法 Article III 强制)

data_scope 改写规则测试覆盖率必须 100%,覆盖以下用例清单并通过。

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
  9. CUSTOM,显式拒绝(fail-closed)
  10. 改写后再过防火墙,防御性校验生效
