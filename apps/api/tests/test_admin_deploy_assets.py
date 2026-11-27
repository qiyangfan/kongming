"""管理台部署资产:同源 /api、CSP、旧 /admin 308 的静态守卫。"""

from pathlib import Path

from httpx import AsyncClient

ROOT = Path(__file__).resolve().parents[3]


def test_admin_nginx_same_origin_api_and_csp() -> None:
    text = (ROOT / "infra/admin/nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://api:8000/;" in text  # /api 前缀剥离
    assert "location /api/" in text
    assert "connect-src 'self'" in text
    assert "frame-ancestors 'none'" in text
    assert "Access-Control-Allow-Origin" not in text  # 不开放 CORS
    assert "Sec-WebSocket-Protocol" in text
    assert "client_max_body_size" in text  # 默认 1m 会拦 RAG 文档上传
    assert "proxy_read_timeout 300s" in text  # 长任务不被默认 60s 截断


def test_web_admin_redirect_rule() -> None:
    text = (ROOT / "infra/web/admin-redirect.conf").read_text(encoding="utf-8")
    assert "return 308" in text
    assert "admin_rest" in text
    assert "${KM_ADMIN_PUBLIC_ORIGIN}" in text


async def test_api_does_not_send_cors_headers(client: AsyncClient) -> None:
    r = await client.get("/healthz", headers={"Origin": "http://127.0.0.1:5174"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
