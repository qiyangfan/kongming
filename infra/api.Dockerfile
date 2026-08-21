FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
EXPOSE 8000
# --proxy-headers(015 R1-B6):反代部署下 uvicorn 采信 X-Forwarded-For,
# 审计 ip 列记真实客户端而非网关地址;--forwarded-allow-ips 限内网网段
CMD ["uv", "run", "uvicorn", "kongming.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
