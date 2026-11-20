# 从仓库根目录构建，确保 packages/liannu 路径依赖位于构建上下文中。
# 构建:podman build -f infra/agent.Dockerfile -t kongming-agent:test .
# 基础镜像按 digest 固定(供应链);tag 仅作可读性,Dependabot docker 会同时更新两者
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS deps
WORKDIR /app
# 路径依赖源(与 apps/agent/pyproject.toml 的 ../../packages/liannu 对齐)
COPY packages/liannu/ packages/liannu/
COPY apps/agent/pyproject.toml apps/agent/uv.lock apps/agent/
# --no-editable:path 依赖拷入 site-packages,运行时镜像不再需要源码树
WORKDIR /app/apps/agent
RUN uv sync --frozen --no-dev --no-install-project --no-editable

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca
WORKDIR /app
# 基础镜像发布时的 Debian 包已有可修复漏洞(实测 7 CRITICAL/多项 HIGH),
# 构建期升到当前安全补丁;升级后的同构镜像 trivy 扫描为 0
# hadolint ignore=DL3005
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2)"]

RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid 10001 --home-dir /app app
USER 10001:10001
# 只取 .venv 与显式应用目录——不整目录 COPY apps/agent
COPY --from=deps /app/apps/agent/.venv /app/apps/agent/.venv
COPY apps/agent/kongming_agent apps/agent/kongming_agent
COPY apps/agent/pyproject.toml apps/agent/
ENV PATH="/app/apps/agent/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/apps/agent/.venv"
WORKDIR /app/apps/agent
EXPOSE 8001
CMD ["uvicorn", "kongming_agent.main:app", "--host", "0.0.0.0", "--port", "8001"]
