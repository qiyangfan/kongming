# 004 设计:受控执行路由

## Context

现状:`internal/router.py` 仅防火墙桩(过检返回空集);`sqlfw/firewall.py`
(sqlglot AST 白名单)已就绪;baseline 已有 sys_sql_audit/sys_role.data_scope,
缺 sys_dept 与 sys_user.dept_id(baseline 缓建)。上游参考:DataScopeApplier
(JSqlParser)与 DataScopeService(意图计算)已验证的「意图/改写分离」结构,
本设计取其逻辑分层,AST 栈换 sqlglot(与防火墙同库,不引新依赖)。

## Goals / Non-Goals

**Goals:** 执行路由全链路成真;datascope 十条清单测试先行;审计三类落库。
**Non-Goals:** 见 proposal(动态池/LLM 接线/查询接口均不在本变更)。

## Decisions

### D1 改写策略:最外层 WHERE AST 注入(004 集成测试修正)

首版按主 spec 字面实现「包最外层子查询 + 外层 WHERE」,被集成测试证伪:
原查询不投影过滤列时(SELECT mark FROM goods),过滤列对外层子查询不可见,
执行必报 column does not exist。修正为上游验证过的形态——谓词 AND 进
最外层查询 WHERE,列引用限定主表别名(JOIN 防歧义):
过滤列属于 FROM 主表必然可见,与原查询投影/聚合形态解耦;
UNION / FROM 子查询取不到主表 → 安全降级(不改写 + 告警,同上游)。
主 spec 三处「包子查询」措辞由本变更 delta 修正(见 specs/datascope)。
代价:JOIN 双方同名过滤列理论歧义 → 以主表别名限定消解。

### D2 值注入:绑定参数,不按字面量拼接

改写产出 (sql, params) 二元组:谓词值用命名占位(`:km_ds_0…`),
按列存在性探测同时读出的 data_type 决定绑定 Python uuid 还是 str
(uuid 列绑 UUID,其余绑 str)——避免 PG uuid=text 算子不存在报错,
同时满足 spec「AST 表达式层绑定参数注入」。

### D3 列存在性探测:information_schema,schema 双限定

探测查询限定 `table_schema IN (tenant_<key>, 'public')`(上游 009 跨 schema
同名误判的修复平移)。提取主表名:sqlglot 取最外层 Select 的 FROM 为
exp.Table 时的表名;取不到(UNION、FROM 子查询)则安全降级(不改写+告警),
与上游行为一致。

### D4 多角色取最宽松:显式宽松序,不依赖存储序

VARCHAR 枚举无数值序,代码内置 ranking:ALL(0) < CUSTOM(1) <
DEPT_AND_BELOW(2) < DEPT(3) < SELF(4),取最小值。与上游 SMALLINT
1~5 的 MIN 语义逐档对应;CUSTOM 排第二——用户若含 CUSTOM 角色即
fail-closed 拒绝,与上游一致(种子不建 CUSTOM 角色,常态不触发)。

### D5 只读执行池:独立引擎,连接级只读

默认数据源执行引擎与控制面写引擎物理分离:connect_args
server_settings `default_transaction_read_only=on`(连接级只读,
spec 原话)+ `statement_timeout` 兜底。池 size=5/max_overflow=0。
引擎按 key 缓存(get 惰性建),本变更只注册 'default';sys_datasource
行动态池后续插槽。km_readonly(NOLOGIN 组角色)不直接可用,
建专用登录角色属 pg-init/运维面变更,留 Open Question。

### D6 事务级 search_path:SET LOCAL,标识符经引用

同一事务:`SET LOCAL search_path TO "tenant_<key>", public` → 执行 SQL。
tenant_key 来自库表且经双引号转义(防标识符注入)。SET LOCAL 随事务
结束自动失效,连接归还池不带残留(连接级只读下 SET LOCAL 是允许项)。

### D7 审计:独立短事务写,失败不阻断

审计写走控制面引擎独立 session(不与应用查询同事务),执行完即插;
写入异常仅 error 日志。trace_id 不做格式校验(调用方约定 ULID/UUID 均可,
VARCHAR(64) 截断防御:超长截断落库并告警)。

### D8 分层落点(宪法 Article II)

- `kongming/datascope/`:models(ScopeFilter 意图/枚举/ranking)+
  service(纯逻辑 resolve,零框架)与 rewrite(sqlglot 改写,基础设施性质
  单列模块)+ repository(SQLAlchemy:角色 scope/用户 dept/递归 CTE/列探测)。
- `kongming/audit/`:models + writer 仓储(单表插入,无 service 层必要)。
- `kongming/internal/`:executor(只读引擎+search_path+截断)与
  service(编排:解析→复校→防火墙→改写→复检→执行→审计),router 只做适配。
- import-linter 新增约定:datascope/audit 域禁 import fastapi。

## Risks / Trade-offs

- [包子查询后外层 WHERE 对无索引列全扫] → 业务表多无过滤列(走降级不
  改写),有过滤列的表属少量;可接受,后续按实测加列类型感知优化。
- [审计与执行两引擎,审计写失败丢审计] → error 日志 + 后续指标暴露;
  备选(同事务)会引入写连接进只读链路,拒绝。
- [FROM 子查询/UNION 一律降级不过滤] → 与上游行为一致;NL2SQL 生成物
  多为单表/JOIN,命中改写路径;降级有审计告警可观测。

## Migration Plan

迁移 0003 纯加法:`sys_dept`(id UUID v7 PK、parent_id 自引用 NULL=根、
dept_name、tenant_id FK、sort、status CHECK、timestamptz)+
`sys_user.dept_id`(NULLable FK sys_dept)+ 两索引。存量行 dept_id=NULL
→ DEPT 系 scope 安全降级(与 spec「未绑定部门降级」一致)。回滚 = down
迁移删列删表,无数据依赖。

## Open Questions

- 专用只读登录角色(km_readonly 授予具体 login 账号)属 pg-init/部署面,
  随动态数据源变更一并评估;当前连接级只读 + 防火墙已构成双闸。
