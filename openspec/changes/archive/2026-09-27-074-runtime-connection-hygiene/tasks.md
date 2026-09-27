# 074-runtime-connection-hygiene 实施任务

## 1. 测试先行:配置生效与坏连接恢复(宪法 Article II/III)

- [x] 1.1 失败测试:`Settings` 三字段(`db_pool_size`/`db_max_overflow`/
      `db_pool_recycle_sec`)缺省值为现状值(5/5/关),
      使既有部署行为零变化;先跑红
- [x] 1.2 失败测试:engine 构造参数随配置变化(注入配置后断言
      `pool_size`/`max_overflow`/`pool_recycle`/`pool_pre_ping`);先跑红
- [x] 1.3 失败测试:连接被对端关闭后,后续请求自动换连接成功
      (以测试库 `pg_terminate_backend` 杀掉池内连接,再发一次查询);
      先跑红

## 2. 控制面接线

- [x] 2.1 `config.py` 增三个 Settings 字段 + 校验(取值范围非负);
      登记 `.env.example`(注释说明语义与缺省值)
      **验证**:1.1 转绿
- [x] 2.2 `db.py` engine 增加 `pool_pre_ping=True` 与可配
      `pool_recycle`/容量;其余参数不变
      **验证**:1.2、1.3 转绿;api 既有测试全集零回归

## 3. 部署栈就绪依赖

- [x] 3.1 `infra/docker-compose.yml` 为 api/agent 声明 healthcheck
      (打各自 `/healthz`;agent 的端点已豁免内部令牌中间件);
      agent 依赖改为 `condition: service_healthy`
      **验证**:`podman compose up -d` 后 `inspect` 显示 agent 在 api healthy
      之后启动;冷启动后首轮问答不因端口未 listen 失败
- [x] 3.2 api/agent/redis 补 `restart: unless-stopped`
      **验证**:kill 掉 api 进程后容器自动重启并恢复服务
- [x] 3.3 与 060(fix-container-image-build)的 compose 改动核对:
      本变更只增 healthcheck/condition/restart,不改 build context
      **验证**:两变更若并行,`git diff` 按行域无冲突;合并后 `compose config`
      校验通过

## 4. 收口

- [x] 4.1 新建能力域 spec:`openspec/specs/runtime-resilience/spec.md`
- [x] 4.2 手工冒烟:完整起栈 → `/healthz` 双绿 → 提一次问答 →
      `pg_terminate_backend` 杀掉 backend → 再提一次问答确认自动恢复;
      kill api 容器确认自动重启;记录于 apply
- [x] 4.3 全量 `make check` 收口

> 074 落地记录:
> - Settings 增 db_pool_size/max_overflow/pool_recycle_sec(缺省 5/5/关 零变化);
>   db.py engine 开 pool_pre_ping,容量与换代可配。
> - compose:api/agent healthcheck(/healthz)、agent depends_on service_healthy、
>   api/agent/redis restart: unless-stopped。
> - **部署面补丁**:主机端口写死与本机 podman-machine gvproxy/并行项目撞车,
>   改为 `KM_REDIS_HOST_PORT`/`KM_API_HOST_PORT`/`KM_AGENT_HOST_PORT` 可配。
> - 新建 runtime-resilience 能力域 spec;测试 2 例。
> - **冒烟已过**:compose 全栈起,healthz 双绿,kill api 进程后自动重启恢复 200。
