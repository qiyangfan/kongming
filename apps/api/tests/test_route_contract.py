"""路由契约不变式:每个 JSON 路由必须显式声明 response_model。

不声明时 FastAPI 会把 dict 原样回传:多出的内部字段会泄漏,OpenAPI/生成类型
里也不会出现该响应的形状。新增免声明的路由须在此显式登记后放行。
"""

from collections import Counter
from typing import Any, cast

from fastapi.routing import APIRoute

from kongming.main import app


def test_every_route_declares_response_model() -> None:
    bare = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.response_model is None
    ]
    assert bare == []


def test_operation_ids_are_unique() -> None:
    """对生成后的 OpenAPI 校验:自定义 operation_id 重名会让客户端方法互相覆盖。"""
    spec: dict[str, Any] = app.openapi()
    ids: list[str] = []
    paths = cast("dict[str, dict[str, Any]]", spec["paths"])
    for path_item in paths.values():
        for op in path_item.values():
            operation_id = op.get("operationId")
            if isinstance(operation_id, str):
                ids.append(operation_id)
    duplicated = [name for name, count in Counter(ids).items() if count > 1]
    assert duplicated == []
