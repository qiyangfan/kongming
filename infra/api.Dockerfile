# 060:build context = 仓库根(packages/liannu 路径依赖落在 context 内)
# 构建:podman build -f infra/api.Dockerfile -t kongming-api:test .
# stack-refresh 批④:3.13→3.14(ctranslate2 cp314 轮子已确认 4.8.3;
# 任一依赖失配整批回退 3.13,design D6)
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS deps
WORKDIR /app
# 路径依赖源(与 apps/api/pyproject.toml 的 ../../packages/liannu 对齐)
COPY packages/liannu/ packages/liannu/
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/
# --no-editable:path 依赖拷入 site-packages,运行时镜像不再需要源码树
RUN cd apps/api && uv sync --frozen --no-dev --no-install-project --no-editable

# stack-refresh 批④:3.13→3.14(ctranslate2 cp314 轮子已确认 4.8.3;
# 任一依赖失配整批回退 3.13,design D6)
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim
WORKDIR /app
# 只取 .venv 与显式应用目录——不整目录 COPY apps/api,避开 var/ 与宿主机 .venv
COPY --from=deps /app/apps/api/.venv /app/apps/api/.venv
COPY apps/api/alembic apps/api/alembic
COPY apps/api/kongming apps/api/kongming
COPY apps/api/alembic.ini apps/api/pyproject.toml apps/api/
ENV PATH="/app/apps/api/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/apps/api/.venv"
WORKDIR /app/apps/api
EXPOSE 8000
# --proxy-headers(015 R1-B6):反代部署下 uvicorn 采信 X-Forwarded-For,
# 审计 ip 列记真实客户端而非网关地址;--forwarded-allow-ips 限内网网段。
# 三网段必须逗号拼成单参数——原 CMD 把它们写成独立 argv,uvicorn 直接拒收
# (060 前镜像构建必然失败,该缺陷从未被跑出来)
CMD ["uvicorn", "kongming.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"]
