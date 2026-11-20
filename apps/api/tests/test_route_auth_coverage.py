"""路由鉴权覆盖:除健康检查外,所有 API 路由必须挂载 current_user(直接或经 require_perm)。

新增公开端点必须显式加入 ALLOW_PUBLIC,让「忘记挂鉴权」在测试层立即暴露,
而不是等安全评审或线上探测。
"""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from kongming.main import app

ALLOW_PUBLIC = {("GET", "/healthz")}


def _has_current_user(dep: Dependant) -> bool:
    """递归检查依赖树是否包含 current_user(require_perm 内部也挂它)。"""
    for sub in dep.dependencies:
        if getattr(sub.call, "__name__", "") == "current_user":
            return True
        if _has_current_user(sub):
            return True
    return False


def test_every_route_requires_auth() -> None:
    public: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or _has_current_user(route.dependant):
            continue
        methods: set[str] = route.methods or set()
        for method in methods - {"HEAD", "OPTIONS"}:
            public.add((method, route.path))
    assert public == ALLOW_PUBLIC
