# 002 Tasks — 认证闭环

> 纪律:安全规则类(登录/锁定/令牌轮换/联邦 state)测试先行——先写失败测试再实现;
> 每完成一组跑对应 `make check` 子集并在此记录结果。

## 1. 迁移与基础设施

- [x] 1.1 `alembic/versions/0002_user_identities.py`:建 user_identities(五家 CHECK 一次建全、
      双唯一约束)+ sys_user 旧列数据平移 + 删 auth_source/external_id;downgrade 逆向;
      本地 `alembic upgrade head` + `downgrade 0001` + 再 upgrade 往返验证
- [x] 1.2 `kongming/db.py`:async engine/session 工厂 + get_session 依赖 + uuid7 生成器(内联实现,不引新依赖)
- [x] 1.3 `kongming/redis_client.py`:Redis 单例 + get_redis 依赖
- [x] 1.4 `config.py` 新增:KM_ADMIN_USERNAME/KM_ADMIN_INITIAL_PASSWORD、登录锁定阈值/时长、
      state TTL、KM_GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI;`.env.example` 同步增补

## 2. 测试先行:认证安全语义(先写全部失败测试)

- [x] 2.1 conftest:kongming_test 建库 + 程序化 alembic upgrade head;用例级 TRUNCATE;
      Redis db15 FLUSHDB;provider HTTP 交换 mock 替身
- [x] 2.2 登录测试:成功/无差别 401(文案+时耗拉平)/5 次失败锁定 429 且锁定期正确密码亦拒/
      禁用 403/软删拒登/must_change_password 标志/成功后计数清零
- [x] 2.3 令牌测试:轮换出新对且旧票即刻失效/旧票复用→家族吊销→同家族新票亦 401/
      logout 吊销/refresh 当 access 用 401/改密后全家族吊销
- [x] 2.4 /auth/me 测试:真实角色+权限点并集/无角色为空列表/租户清单/禁用后实时 401/无令牌 401
- [x] 2.5 改密测试:正常改密+标志消除+家族吊销/当前密码错误 400/纯联邦账号 400
- [x] 2.6 联邦测试:options 过滤未配置/start 下发 state(google 带 code_challenge)/
      state 复用·过期·伪造 401/未配置·未注册 provider 404/回调成功签发且响应不含 OAuth token
- [x] 2.7 account_link 测试:identity 命中直连/verified email 关联/未验证 email 不关联自动建号/
      用户名冲突后缀/多身份绑定/禁用联邦账号 403/注册事件落 oper_log
- [x] 2.8 种子测试:空库首启播种齐(角色/权限点/默认租户/超管/绑定)/重复启动幂等/
      env 密码不强制改密 vs 缺省密码强制改密

## 3. auth 域实现(测试转绿)

- [x] 3.1 models.py + ports.py:纯 dataclass/枚举 + 全部 Protocol(零框架)
- [x] 3.2 repository.py:SQLAlchemy 实现(user/role/tenant/identity/oper_log)
- [x] 3.3 stores.py:Redis 实现(refresh 家族三态/登录计数与锁/oauth state GETDEL)
- [x] 3.4 tokens.py 扩展:refresh 携带 fid+jti;service.py 登录/轮换/吊销/logout/改密/me
- [x] 3.5 providers/base.py 抽象+注册表;providers/github.py(迁移既有 github.py 能力)、
      providers/google.py;旧 auth/github.py 删除
- [x] 3.6 service.py 联邦:oauth_start/oauth_callback/account_link(单事务)
- [x] 3.7 seed.py:权限点注册表 + 只增不改幂等播种;main.py lifespan 接入(启动即播)
- [x] 3.8 router.py 重写:login/refresh/logout/me/password/change/options/{provider}/start|callback;
      领域异常→状态码统一映射
- [x] 3.9 import-linter 契约核对(auth 不反向依赖);`make check` api 子集绿

## 4. 契约与前端

- [x] 4.1 `make contracts`:openapi.json 重导出 + 前端类型重生成(产物随代码)
- [x] 4.2 web api 客户端:refresh 存 localStorage/access 存内存、启动换发、401 单飞刷新重放、logout 清态
- [x] 4.3 zustand auth store + 路由守卫(未认证→/login;must_change→/change-password)
- [x] 4.4 登录页接线:账密提交存令牌跳转;`/auth/options` 驱动联邦按钮(GitHub/Google SVG 内联)
- [x] 4.5 `/auth/:provider/callback` 前端路由:收 code+state → POST 后端 → 存令牌 → 跳 /
- [x] 4.6 /change-password 页 + 占位首页(用户卡/perm 清单/退出);`make check` web 子集绿

## 5. CI 与端到端

- [x] 5.1 ci.yml api job 增 postgres(pgvector 同源镜像)+ redis services 与 KM_* 环境注入
- [x] 5.2 全量 `make check` 绿(api/agent/web/契约幂等)
- [x] 5.3 端到端冒烟(记录于 apply):空库冷启→种子→admin 登录→me→刷新→旧票复用 401→
      改密→重新登录→options/start 返回形态→web 5173 登录页全链路
      实测记录:curl 八步全过(me 返回 SUPER_ADMIN+双权限点+default 租户;复用 401 且家族吊销;
      改密后旧密码 401/新密码 must_change=false;5 连败第 5 次即 429;未配置联邦 options 空+start 404);
      浏览器实测(vite 落 5174,5173 被旧项目占):登录页渲染(未配置联邦无按钮)→账密登录→
      首页占位(角色/权限点/租户)→刷新会话恢复→登出回登录页;控制台仅 favicon 404。
- [x] 5.4 HANDOFF.md 更新(当前状态/下一步梯队刷新)

## 验收

- [x] spec「账密登录/令牌轮换/当前用户/自助改密/联邦登录/账号关联」六组场景全部有测试或冒烟对应
- [x] rbac spec「种子数据」场景:空库启动幂等播种实测通过
- [x] 空库冷启动一条命令可登录;锁定/吊销/联邦安全语义全部测试锁死
