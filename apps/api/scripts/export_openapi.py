"""导出 OpenAPI 到 contracts/openapi.json(ADR-005 契约管线)。

CI 门禁:导出后 git diff --exit-code contracts/,防止契约漂移。
用法:uv run python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / "contracts" / "openapi.json"

# SSE 端点声明的事件流媒体类型;FastAPI 对 responses 的 model 键自动补
# application/json,而流式端点成功时只返回事件流,导出时定点移除
SSE_PATHS = {"/chat/query/stream"}


def _drop_default_json_content(schema: dict[str, Any]) -> None:
    """移除 SSE 端点 200 响应里 FastAPI 自动补的 application/json 内容。"""
    paths = cast("dict[str, Any]", schema.get("paths", {}))
    for path, methods in paths.items():
        if path not in SSE_PATHS:
            continue
        for op in cast("dict[str, Any]", methods).values():
            responses = cast("dict[str, Any]", op).get("responses", {})
            r200 = cast("dict[str, Any] | None", responses.get("200"))
            content = cast("dict[str, Any] | None", (r200 or {}).get("content"))
            if content and "application/json" in content:
                del content["application/json"]


def main() -> int:
    from kongming.main import app

    schema = app.openapi()
    _drop_default_json_content(schema)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"exported → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
