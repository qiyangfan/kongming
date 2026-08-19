# 008 动态数据源 — 任务

> 安全规则类测试先行(先写失败测试再实现);每个任务组完成后跑对应
> make check 子集。冒烟方案(同库异 schema 假动态源 vs 独立库)apply 期定。

## 1. schema 与加密基建

- [ ] 1.1 0005 迁移:sys_datasource 补 name/default_schema 列(ADR-004;downgrade 注记);`uv run alembic upgrade head` 验证
- [ ] 1.2 AES-GCM cipher(cryptography):base64(iv‖ct+tag) 加解密 round-trip 测试先行;密钥缺省 dev 降级(常量+告警)、非法密钥长度/坏密文错误路径
- [ ] 1.3 config:datasource_encrypt_key 消费方接线;.env.example 注记密钥生成方法

## 2. datasource 域包(测试先行)

- [ ] 2.1 失败测试:注册加密落库/DTO 无密文/key 唯一(含禁用)/保留字 default/pattern 拒绝/default_schema 注入拒绝/key+租户归属不可改/非超管 403/创建租户归属显式指定(隐式上下文不生效+指定租户存在性校验)
- [ ] 2.2 models+ports+repository(Datasource 模型、Repo/Cipher/PoolEvictor Protocol、SQLAlchemy 仓储)
- [ ] 2.3 service 用例:page(全局视角不限当前租户,关键字 ILIKE 转义)/create(租户归属显式指定)/update(密码留空不改+连接信息变更检测)/enable/disable/delete(物理删);同事务审计 5 动作;commit 后 PoolEvictor 调用(仅连接信息变更/启停/删除,evict 失败仅告警不阻断)
- [ ] 2.4 router `/system/datasource/*`(datasource 域独立 router,非 system 域包):page/create/update/enable/disable/delete/test/options;领域错误统一映射(404/409/400);require_perm 挂载;管理端点 X-Tenant-Id 无语义
- [ ] 2.5 seed:PERMISSIONS 增 system:datasource:manage(排序 4),SUPER_ADMIN 绑定自动覆盖验证
- [ ] 2.6 import-linter:datasource models/service/ports 零框架契约;**修「auth 域不反向依赖业务域」forbidden_modules:datasource_admin→kongming.datasource**(R1-S2 防线静默失效;rag_admin 猜测名挂账 rag 变更);`make api-check` 绿

## 3. 测试连接端点

- [ ] 3.1 失败测试:成功 ok/超时/认证失败原因不暴露密码/不落库不建池**不落审计**/编辑态留空取库存密文/密文不可解归「凭证不可用」类不暴露细节
- [ ] 3.2 实现:asyncpg 直连 timeout=3s SELECT 1;失败 reason 分类收敛(驱动类别+凭证不可用)

## 4. 动态池与执行路由(测试先行)

- [ ] 4.1 失败测试:非 default 全链路 PASS/不存在+禁用+跨租户同形 400 DATASOURCE_INVALID(不静默回退)/data_scope 改写在动态源生效(SELF scope 实证,探测集 {default_schema, public})/防火墙+保留表+危险函数在动态源同拦/**schema 边界校验(CROSS_SCHEMA)在动态源生效(边界集 {default_schema, public})**/search_path=default_schema+public 事务级/池只读/查询面 datasource_key pattern 拒绝(chat+internal)
- [ ] 4.2 executor:get_or_create_engine(asyncio.Lock 防双池)/evict_engine 定向失效(失败仅告警)/参数与 default 同构/**lifespan teardown 接线 dispose_read_engines**(R1-A1:现状无调用点)/search_path 双引号包裹+转义与默认路径同款
- [ ] 4.3 internal/service:移除 SUPPORTED_KEYS 硬编码,DatasourceResolver 校验+解密+动态池路由;probe_column_type 换动态连接(schemas=[default_schema]);审计 datasource_key 语义不变
- [ ] 4.4 池生命周期联动测试:改密码后旧池失效新凭证重建/**启用后按最新连接信息重建池**/仅改 name 不动池/删除后按不存在拒

## 5. internal-meta 动态源(测试先行)

- [ ] 5.1 失败测试:动态源限 default_schema 内省/非法 key 同形拒绝/保留表排除同判定/默认路径零回归
- [ ] 5.2 实现:meta.py 移除 fail-closed 硬编码,复用 DatasourceResolver+动态池

## 6. chat 入口预检(测试先行)

- [ ] 6.1 失败测试:伪造/禁用/跨租户 key 400 同形且不建会话不调 agent;合法 key 全链路透传
- [ ] 6.2 chat/service 注入 DatasourceChecker 端口,租户解析后预检

## 7. 契约与前端

- [ ] 7.1 `make contracts` 重生成并提交产物;web 类型消费零手写
- [ ] 7.2 /admin/datasources 页:列表+分页+搜索/创建编辑对话框(key+schema 禁改回显/**租户下拉显式指定(复用 007 租户只读清单 /system/tenants——该端点挂 system:user:manage,与数据源页权限点不同源,当前仅超管持有全集无实际影响,R2 登记备查)**/密码留空提示)/启停/删除受控确认(007 教训:成功才关)/测试连接按钮;AdminLayout 子导航第四项(perm 过滤)
- [ ] 7.3 对话页数据源切换器:options 接口(首位 default 合成项,租户复校查询在 datasource repository 自备)/localStorage km.datasource/切租户重置/react-query 键含数据源;/chat/query 携带 key;**api.ts「头对非 chat 端点无语义」注释同步修订**(options 为例外消费方)
- [ ] 7.4 web tsc+eslint 绿;shadcn 新组件语义类核查(v4 基线三件套纪律)

## 8. 端到端冒烟与收尾

- [ ] 8.1 curl 链路:登录→注册(查库验密文)→测试连接→options→/chat/query 动态源全链路(真 LLM)→禁用即拒→改密池失效→删除回收→审计五动作可查
- [ ] 8.2 Playwright 冒烟:管理页 CRUD+测试连接、切换器选中随查询发送、smoke-self 无权 403+选项接口仅见 default
- [ ] 8.3 越权面复验:跨租户 key/保留字/schema 注入/非超管 403 perm_denied 审计
- [ ] 8.4 `make check` 全绿;openspec validate --strict;HANDOFF 更新
