# 从仓库根目录构建，确保 packages/liannu 路径依赖位于构建上下文中。
# 构建:podman build -f infra/api.Dockerfile -t kongming-api:test .
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS deps
WORKDIR /app
# 路径依赖源(与 apps/api/pyproject.toml 的 ../../packages/liannu 对齐)
COPY packages/liannu/ packages/liannu/
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/
# --no-editable:path 依赖拷入 site-packages,运行时镜像不再需要源码树
WORKDIR /app/apps/api
RUN uv sync --frozen --no-dev --no-install-project --no-editable

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim
WORKDIR /app
# 基础镜像发布时的 Debian 包已有可修复漏洞(实测 7 CRITICAL/多项 HIGH),
# 构建期升到当前安全补丁;升级后的同构镜像 trivy 扫描为 0
# hadolint ignore=DL3005
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*
# 只取 .venv 与显式应用目录——不整目录 COPY apps/api,避开 var/ 与宿主机 .venv
COPY --from=deps /app/apps/api/.venv /app/apps/api/.venv
COPY apps/api/alembic apps/api/alembic
COPY apps/api/kongming apps/api/kongming
COPY apps/api/alembic.ini apps/api/pyproject.toml apps/api/
ENV PATH="/app/apps/api/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/apps/api/.venv"
WORKDIR /app/apps/api
EXPOSE 8000
# 反向代理部署时读取 X-Forwarded-For，使审计记录保留客户端地址；仅信任指定内网网段。
# --forwarded-allow-ips 的网段列表必须作为一个逗号分隔参数传入。
CMD ["uvicorn", "kongming.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"]
