"""导出 OpenAPI 到 contracts/openapi.json(ADR-005 契约管线)。

CI 门禁:导出后 git diff --exit-code contracts/,防止契约漂移。
用法:uv run python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / "contracts" / "openapi.json"


def main() -> int:
    from kongming.main import app

    schema = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"exported → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
