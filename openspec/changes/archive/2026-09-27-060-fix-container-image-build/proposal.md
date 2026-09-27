# 060-fix-container-image-build 提案:部署镜像构建修复与镜像门禁

## Why

`infra/api.Dockerfile` 与 `infra/agent.Dockerfile` **构建必然失败**,已实测复现(两次构建,报错逐字相同):

```text
STEP 4/7: RUN uv sync --frozen --no-dev --no-install-project
error: Could not compute absolute path from workspace root and lockfile path
  Caused by: cannot normalize a relative path beyond the base directory: /app/../../packages/liannu
```

两个进程以 `[tool.uv.sources] liannu = { path = "../../packages/liannu" }` 引入 monorepo 共享引擎包,而 build context 分别是 `apps/api` 与 `apps/agent`,路径依赖落在 context 之外,uv 拒绝解析。

`infra/docker-compose.yml` 的 api/agent 服务正是用 `build:` 指向这两个 Dockerfile,`docs/HANDOFF.md` 亦明示「compose 的 api/agent 服务留完整部署用」——即**当前唯一一条完整部署链路是断的**。CI 五个 job(api/agent/liannu/web/contracts)无一构建镜像,断裂可无限期静默。

同时两个 Dockerfile 都没有 `.dockerignore`(全仓 0 个),`COPY . .` 会把 `apps/api/var/` 下 25 个真实上传附件与一份含真实 user_id/action/ip 的审计归档 `.jsonl.gz`、以及宿主机的 macOS `.venv` 一并烤进镜像层——后者已实测使 `uv run` 打印 `Ignoring existing virtual environment linked to non-existent Python interpreter` 后销毁重建。

`infra/sandbox.Dockerfile` context 为 `infra/` 且无路径依赖,自洽无需改动(已核)。

## What Changes

1. **build context 提升到仓库根**,两个 Dockerfile 多阶段化:deps 阶段只 `COPY packages/liannu/` 与 `apps/{api,agent}/pyproject.toml` + `uv.lock` 后执行 `uv sync`;runtime 阶段 `COPY --from=deps` 传入 `.venv`,并显式拷贝应用目录(`alembic`/`kongming`、`alembic.ini`、`pyproject.toml`),**不整目录 `COPY apps/*`**(同时避开 var/ 与宿主机 .venv)。
2. **新增根 `.dockerignore`**:`.venv/`、`**/.venv/`、`node_modules/`、`dist/`、`.git/`、`apps/api/var/`、`**/__pycache__/`、`.env`、`*.pem`、`.mypy_cache/`、`.ruff_cache/`、`.pytest_cache/`。
3. `infra/docker-compose.yml` 的 api/agent `build` 改为 `context: .` + `dockerfile: infra/{api,agent}.Dockerfile`。
4. **CI 新增第 6 个门禁 `images`**:两个 Dockerfile 构建(不推送),失败即 job 失败,与既有 job 同 concurrency 策略。
5. `README.md` / `AGENTS.md` 中的镜像构建命令按新 context 更新,避免留下幽灵命令。

## Non-goals

- **镜像推送 registry / 多架构构建(arm64+amd64)**:仓库无 registry 配置与发布策略,属发布链变更,另立 change。
- **产物体积优化**(当前 api 镜像约 309 MB,含 uv 与解释器):distroless、uv cache 清理、层数压缩另立;本变更只求「能构建且入门禁」。
- **`infra/sandbox.Dockerfile` 纳入同构改造**:其 context 自洽、无路径依赖,改造只增风险不增益。
- **构建缓存策略调优**(BuildKit cache mount、依赖层缓存命中率):性能项,另立。
- **健康检查探针 / 优雅停机 / 多 worker**:属运行时形态,与构建修复正交。

## Capabilities

(无——构建与 CI 基础设施变更,不产生用户可见行为;同 `054-gates-env-parity` 形态,不新增 spec delta。)

## Impact

- **落点**:`infra/api.Dockerfile`、`infra/agent.Dockerfile`(重写)、`infra/docker-compose.yml`、`.dockerignore`(新增)、`.github/workflows/ci.yml`、`README.md`/`AGENTS.md` 命令段。
- **无 schema 变更**(不触 Alembic,ADR-004);**无 OpenAPI 契约变更**(无端点/模型变更,ADR-005 零触发);**无需新 ADR**(部署单元形态未变,仍是 api/agent 双进程,ADR-002 信任边界不变)。
- **已按本提案形态验证**:api 镜像构建成功(约 309 MB),容器内 `from kongming.main import app` 导入正常(38 条路由)。两进程镜像均需在 apply 阶段复验。
