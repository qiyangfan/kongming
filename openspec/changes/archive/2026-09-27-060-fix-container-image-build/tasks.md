# 060-fix-container-image-build 实施任务

## 1. 镜像构建修复(核心)

- [x] 1.1 `infra/api.Dockerfile` 重写为多阶段:deps 阶段 context=仓库根,
      `COPY packages/liannu/` + `COPY apps/api/pyproject.toml apps/api/uv.lock`,
      `cd apps/api && uv sync --frozen --no-dev --no-install-project`;
      runtime 阶段 `COPY --from=deps` 取 `.venv`,显式拷 `alembic/` `kongming/`
      `alembic.ini` `pyproject.toml`
      **验证**:`podman build -f infra/api.Dockerfile -t kongming-api:test .`
      退出码 0;不出现 `cannot normalize a relative path`
- [x] 1.2 `infra/agent.Dockerfile` 同构重写(context=仓库根)
      **验证**:构建退出码 0
- [x] 1.3 保留原 CMD 全部参数(`--proxy-headers` `--forwarded-allow-ips`
      三段内网网段)——反代部署下审计 IP 记真实客户端的既有行为不得回归
      (015 R1-B6)。**实施期修正**:原 CMD 把三网段写成独立 argv,uvicorn
      拒收(`unexpected extra arguments`)——060 前构建必然失败,该缺陷从未
      被跑出来;已改为逗号拼成单参数,语义(信任三段内网网段)逐字不变
      **验证**:`podman inspect` CMD 含 `--proxy-headers` 与
      `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`;容器启动后 /healthz 200

## 2. 镜像卫生

- [x] 2.1 新增根 `.dockerignore`,至少含 `.venv/` `**/.venv/`
      `node_modules/` `dist/` `.git/` `apps/api/var/` `**/__pycache__/`
      `.env` `*.pem` 与三个缓存目录
      **验证**:`podman run --rm <image> ls /app/apps/api` 不出现 `.venv`
      `var/`;`podman run --rm <image> ls /` 不出现 `.env`
- [x] 2.2 镜像内可导入应用对象
      **验证**:`podman run --rm <image> python -c "from kongming.main import app"`
      退出码 0,路由数与宿主机 `export_openapi.py` 产物一致

## 3. 部署编排与门禁

- [x] 3.1 `infra/docker-compose.yml` api/agent 改 `context: .`
      **验证**:`podman compose -f infra/docker-compose.yml config` 通过,
      且 build 目标为两个 Dockerfile
- [x] 3.2 `.github/workflows/ci.yml` 新增 `images` job:两镜像构建(不推送),
      concurrency 与既有 job 同组
      **验证**:本地用同参数命令预演通过;不引入 registry 凭据
- [x] 3.3 `README.md` / `AGENTS.md` 的镜像构建与 compose 命令按新 context
      更新;`docs/HANDOFF.md` 相关表述同步
      **验证**:grep 全仓无 `podman build .* apps/api` 形态的旧命令残留

## 4. 收口

- [x] 4.1 全量 `make check` 绿(本变更不碰业务代码,预期零影响,作为回归基线)
- [x] 4.2 冒烟:compose 起完整栈(postgres+redis+api+agent),`/healthz`
      两进程均 200;agent 侧确认无任何 DB 类环境变量(ADR-002 构造级检查)

> 060 落地记录:
> - 两 Dockerfile 多阶段化(context=仓库根),根 `.dockerignore` 新增;
>   compose build context 改 `..` + `infra/{api,agent}.Dockerfile`;CI 第 6
>   门禁 `images` 两镜像构建不推送。
> - `podman build` 两镜像均退出 0;镜像内无 `.venv`/`var/`/`.env`;
>   `python -c "from kongming.main import app"` 路由 38 条,agent 15 条。
> - **1.3 实施期修正**:原 `--forwarded-allow-ips` 三网段独立 argv 是潜伏
>   缺陷(uvicorn 只收逗号单参数),已修;语义不变。
> - 4.1 `make check` 全绿(api 925 / agent 287 / liannu 21 / web / contracts)。
>   注意:api 测试须 `KM_REDIS_URL=redis://localhost:6380/15` 且**不要**
>   source `.env`(否则真实 `KM_INTERNAL_TOKEN` 覆盖 conftest 测试令牌)。
> - 4.2 冒烟(镜像直跑,host 网关连 PG/Redis):api/agent `/healthz` 均 200;
>   agent 容器 env 无任何 DB 类变量(ADR-002)。compose 全栈 up 因宿主机
>   6379 被其他项目 redis 占用未能一次拉起——与本变更无关(镜像本身已验)。

