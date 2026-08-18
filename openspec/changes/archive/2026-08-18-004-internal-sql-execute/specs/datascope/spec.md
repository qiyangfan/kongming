# datascope 能力域(004 delta:改写机制措辞修正)

004 集成测试实证:原查询不投影过滤列时(如 SELECT mark FROM goods),
「包外层子查询」使过滤列对外层不可见,执行必错。改写机制修正为
上游验证过的「最外层 WHERE AST 注入」(列限定主表别名,JOIN 防歧义),
安全语义(降级/fail-closed/绑定参数/复检)不变。

## MODIFIED Requirements

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
