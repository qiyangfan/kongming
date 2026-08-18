# sql-execution 能力域(005 delta)

## ADDED Requirements

### Requirement: 控制面保留表拒读

执行路由 SHALL 拒绝任何引用控制面保留表的语句(verdict=BLOCKED,落审计),
与语句其余部分的合法性无关。保留表按表名末段判定(小写折叠语义):
前缀 `sys_`、`chat_`,及精确名 `alembic_version`、`user_identities`。
判定 SHALL 不限定 schema 生效——裸表名经 search_path 可解析到 public
控制表,是最危险形态;CTE 名引用与表函数 SHALL NOT 误伤。
保留前缀平台级生效:租户 schema 由平台开通,SHALL NOT 出现同名表。

背景(005 评审实证):search_path 含 public 且只读角色对 public 全表有
SELECT 时,LLM 可猜中 `sys_user` 等表名直读口令哈希/数据源密文/他人对话;
data_scope 对无 create_by 列的表安全降级不过滤,不构成防线。

#### Scenario: 裸名引用保留表拦截
- GIVEN 语句 `SELECT username, password_hash FROM sys_user`(裸名)
- WHEN 到达执行路由
- THEN verdict=BLOCKED(reason 含 RESERVED_TABLE),不落业务查询,落审计

#### Scenario: 显式 public 限定同样拦截
- GIVEN 语句 `SELECT * FROM public.sys_user`
- WHEN 到达执行路由
- THEN verdict=BLOCKED,落审计

#### Scenario: 系统调用不豁免
- GIVEN 请求不携带 user_id(系统调用)
- WHEN 语句引用保留表
- THEN 同样 BLOCKED(保留表判定与用户身份无关)

#### Scenario: CTE 名与业务表不误伤
- GIVEN 语句含同名 CTE(如 WITH sys_x AS (SELECT ...) SELECT FROM sys_x)
  或纯业务表查询
- WHEN 到达执行路由
- THEN 正常放行执行
