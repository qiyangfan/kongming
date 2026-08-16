# multitenant 能力域

## Purpose

租户白名单真实校验 + 租户切换 + schema 路由。
受影响权限层级: 租户级(sys_user_tenant 白名单)、DB schema 隔离。

## Requirements

### Requirement: 租户白名单真实校验

每次请求按 X-Tenant-Id 头(缺省取用户默认租户)查 sys_user_tenant 真表校验;未命中拒绝。SUPER_ADMIN 不豁免租户白名单。

#### Scenario: 访问已授权的租户放行
- GIVEN sys_user_tenant 存在 (用户, sakila) 绑定
- WHEN 请求携带 X-Tenant-Id: sakila
- THEN 白名单命中,租户上下文传入后续链路

#### Scenario: 访问未授权的租户拒绝
- GIVEN 用户仅绑定 sakila
- WHEN 请求携带 X-Tenant-Id: olist(或伪造租户标识)
- THEN 白名单未命中,返回 403 TENANT_FORBIDDEN
- AND 审计记录此次越权尝试

#### Scenario: 超管不豁免
- GIVEN SUPER_ADMIN 仅绑定 sakila
- WHEN 请求携带 X-Tenant-Id: olist
- THEN 仍拒绝 403 TENANT_FORBIDDEN

设计决策(沿用前系统架构师裁定):SUPER_ADMIN 不豁免租户白名单。依据:
- fail-closed 原则:纵深防御每层独立失败,任何豁免都是攻击面;AWS IAM / Azure PIM / Salesforce 的跨租户访问都必须显式提权并留审计
- 审计合规:超管需跨租户时,通过管理界面显式把自己加入白名单,留下配置记录

管理路径:超管通过用户管理接口显式授权租户绑定,而非系统默认放行。

#### Scenario: 请求头不传租户用默认值
- GIVEN 用户已登录
- WHEN 请求不传 X-Tenant-Id
- THEN 用用户默认租户查白名单(登录时已校验必然命中),放行

### Requirement: 租户切换(前端+后端协同)

租户列表来自 sys_user_tenant 查询;切换租户 = 前端更新 X-Tenant-Id 头(每次请求独立校验白名单,无状态,不重签令牌);切换后数据源选项随之刷新。

#### Scenario: 查询可切换租户列表
- GIVEN 用户绑定 sakila 与 olist
- WHEN 拉取租户列表
- THEN 返回 [{tenantKey:'sakila', tenantName:...}, {tenantKey:'olist', ...}]

#### Scenario: 切换租户后查询命中新租户数据
- GIVEN 用户当前请求租户 sakila,已绑定 olist
- WHEN 前端切换到 olist 并发起查询(头携带 olist)
- THEN 白名单校验通过,查询在 olist 上下文执行(schema 路由到 olist 域)

#### Scenario: 白名单校验在每次请求独立生效
- GIVEN 令牌有效但当前请求携带未绑定租户
- WHEN 请求到达
- THEN 拒绝——租户授权不缓存在令牌里,每请求独立校验(令牌泄漏不放大租户越权面)

### Requirement: 按 tenant 路由 schema(数据源层)

查询执行前在同一事务内设置 search_path 至 `tenant_<key>, public`(事务级,不污染连接池);public 始终在列(系统对象与类型可见性)。

#### Scenario: 查询路由到租户 schema
- GIVEN 当前租户 sakila
- WHEN 查询经控制面执行接口
- THEN 事务内 SET LOCAL search_path TO tenant_sakila, public,SQL 在租户 schema 下执行

#### Scenario: search_path 始终含 public
- GIVEN 任意租户查询
- WHEN 设置 search_path
- THEN 格式为 tenant_<key>, public(系统表 + 向量类型可见性保障)

#### Scenario: 事务级设置不污染连接池
- GIVEN 连接池复用连接
- WHEN 一个请求设置了事务级 search_path
- THEN 事务结束自动恢复,下一请求拿到干净连接

### Requirement: 多租户数据隔离验证(端到端)

schema 路由切换正确;审计正确记录租户。

#### Scenario: 切换租户后查到对应数据
- GIVEN 不同租户 schema 有不同业务数据
- WHEN 切换租户后查询
- THEN SQL 在目标租户 schema 执行,只返回该租户数据

#### Scenario: 审计正确记录租户
- GIVEN 用户切换租户后查询
- WHEN 查询完成
- THEN 审计行 tenant_id 为当前请求租户(非用户默认租户),可按租户维度筛选审计
