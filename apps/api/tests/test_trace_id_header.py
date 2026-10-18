"""061:X-Trace-Id 与请求级 trace_id 单源——自动化断言,不靠人工冒烟。"""

from typing import Any

from httpx import ASGITransport, AsyncClient

from kongming.main import app
from kongming.trace import ensure_trace_id, get_trace_id


async def test_trace_id_header_present_and_propagated() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
        assert r.status_code == 200
        assert "x-trace-id" in r.headers
        assert len(r.headers["x-trace-id"]) >= 16

        r2 = await client.get("/healthz", headers={"X-Trace-Id": "smoke-trace-061-abc"})
        assert r2.headers["x-trace-id"] == "smoke-trace-061-abc"


async def test_ensure_trace_id_stable_within_request() -> None:
    """chat 侧 ensure_trace_id 与中间件写入的 contextvar 同值(1.3 同值契约)。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz", headers={"X-Trace-Id": "same-trace-061"})
        # 请求结束后 contextvar 在该 context 仍可见最后一次写入
        assert r.headers["x-trace-id"] == "same-trace-061"
        # ensure 幂等:已有值则不覆盖
        # (独立 context 里 ensure 会新建;这里验证函数形态)
        tid = ensure_trace_id()
        assert get_trace_id() == tid
        assert ensure_trace_id() == tid


async def test_endpoint_sees_middleware_trace_id() -> None:
    """1.3 同值契约:业务侧 ensure_trace_id 读到的是中间件写入的同一值。"""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from kongming.main import app as _app

    async def _probe(request: Any) -> JSONResponse:
        return JSONResponse({"tid": ensure_trace_id()})

    # 临时挂探针路由(仅测试内;不改生产路由表)。插在根级 Mount("")(adopt-
    # mcp-sdk2 兜底挂载,位次最后)之前——append 会落 Mount 之后恒不可达
    routes = _app.router.routes
    mount_idx = next(i for i, r in enumerate(routes) if type(r).__name__ == "Mount")
    routes.insert(mount_idx, Route("/__trace_probe", _probe))  # pyright: ignore[reportAttributeAccessIssue]
    try:
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/__trace_probe", headers={"X-Trace-Id": "contract-061-same"})
            assert r.status_code == 200
            assert r.json()["tid"] == "contract-061-same"
            assert r.headers["x-trace-id"] == "contract-061-same"
    finally:
        routes.pop(mount_idx)  # pyright: ignore[reportAttributeAccessIssue]
