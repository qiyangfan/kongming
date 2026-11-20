"""Markdown 相对链接校验:文件改名/移动后残留的失效链接在 lint-config 门禁拦下。

只校验形似仓内路径的目标(ASCII 文件名字符),跳过外链、纯锚点,以及正文里
紧跟方括号的括注(如数组字面量 `[id, sum](说明)` 不是链接)。
"""

# ruff: noqa: T201  # CLI 门禁脚本以 stdout 汇报

from __future__ import annotations

import re
import sys
from pathlib import Path

_LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\s]+)\)")
# 路径形态判定:允许字母数字与 . _ - /,可带锚点;中文括注与散文不会命中
_PATHLIKE = re.compile(r"^[A-Za-z0-9._/\-]+(?:#[^)]*)?$")
_SKIP_SCHEMES = ("http://", "https://", "mailto:")
_SKIP_DIRS = {".git", "node_modules", ".venv", "dist"}
_FENCE = re.compile(r"^\s*(```|~~~)")


def _prose(text: str) -> str:
    """去掉围栏代码块:示例里的 markdown 链接不是真链接。"""
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def broken_links(root: Path) -> list[tuple[Path, str]]:
    """返回(root 下 md 文件, 原始链接目标)形式的失效链接清单。"""
    broken: list[tuple[Path, str]] = []
    for md in sorted(root.rglob("*.md")):
        if _SKIP_DIRS.intersection(md.parts):
            continue
        for target in _LINK.findall(_prose(md.read_text(encoding="utf-8"))):
            if target.startswith(_SKIP_SCHEMES) or not _PATHLIKE.match(target):
                continue
            rel = target.split("#", 1)[0]
            # 根相对链接(/docs/x.md)按仓库根解析;相对链接按文件所在目录解析
            resolved = root / rel.lstrip("/") if rel.startswith("/") else md.parent / rel
            if rel and not resolved.exists():
                broken.append((md, target))
    return broken


def main(argv: list[str]) -> int:
    """扫描指定目录(缺省当前目录),有失效链接即非零退出。"""
    root = Path(argv[1]) if len(argv) > 1 else Path()
    broken = broken_links(root)
    for md, target in broken:
        print(f"{md}: {target}")
    print(f"{len(broken)} 个失效相对链接")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
