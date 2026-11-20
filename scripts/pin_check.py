"""镜像基线门禁:infra/ 下 Dockerfile 的基础镜像必须钉到 sha256 摘要。

用法:
  pin_check.py [镜像目录]

只检查构建期会拉取的 FROM 镜像;`FROM deps` 这类阶段别名跳过。
未钉摘要时标签可被上游改写,重扫同一标签也可能拿到不同内容。
"""

# ruff: noqa: T201  # CLI 门禁脚本:检查结果走 stdout,由 make/CI 收集

import re
import sys
from pathlib import Path

_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
_STAGE_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _image_ref(line: str) -> str | None:
    """从一行 FROM 取镜像引用;阶段别名或语法异常返回 None。"""
    parts = line.split()
    tokens = [p for p in parts[1:] if not p.startswith("--")]
    if not tokens or tokens[0].lower() == "as":
        return None
    ref = tokens[0]
    return None if _STAGE_ALIAS.match(ref) else ref


def check(directory: Path) -> list[str]:
    """返回未钉摘要的 FROM 描述(空=通过)。"""
    problems: list[str] = []
    for dockerfile in sorted(directory.glob("*.Dockerfile")):
        for lineno, raw in enumerate(dockerfile.read_text().splitlines(), 1):
            line = raw.strip()
            if not line.upper().startswith("FROM "):
                continue
            ref = _image_ref(line)
            if ref is not None and not _DIGEST.search(ref):
                problems.append(f"{dockerfile.name}:{lineno}: {ref} 未钉 sha256 摘要")
    return problems


def main(argv: list[str]) -> int:
    directory = Path(argv[1] if len(argv) > 1 else "infra")
    problems = check(directory.resolve())
    if problems:
        print("镜像基线门禁失败:")
        for line in problems:
            print(f"  - {line}")
        return 1
    print("镜像基线门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
