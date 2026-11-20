"""依赖许可证门禁:只放行宽松许可,拒绝 copyleft/未知/专有声明。

用法:
  license_check.py python <项目目录>   # 扫描 uv 运行时闭包(不含 dev 依赖)
  license_check.py node <web 目录>    # 扫描 pnpm --prod 依赖

许可串按 AND/OR/分号/括号拆成原子名,逐个比对白名单;
LGPL/GPL/AGPL/UNKNOWN 等任何未列入的原子都会让门禁失败。
"""

# ruff: noqa: T201  # CLI 门禁脚本:检查结果走 stdout,由 make/CI 收集

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

# 原子许可名(大写比对);组合表达式拆开后每个原子都必须在此
PY_ALLOWED = {
    "MIT",
    "MIT-0",
    "MIT LICENSE",
    "BSD",
    "BSD LICENSE",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "3-CLAUSE BSD LICENSE",
    "APACHE",
    "APACHE-2.0",
    "APACHE SOFTWARE LICENSE",
    "ISC",
    "ISCL",
    "ISC LICENSE",
    "ISC LICENSE (ISCL)",
    "PSF",
    "PSF-2.0",
    "MPL-2.0",
    "MPL 2.0",
    "MOZILLA PUBLIC LICENSE 2.0",
    "MOZILLA PUBLIC LICENSE 2.0 (MPL 2.0)",
    "0BSD",
    "ZLIB",
    "CC0-1.0",
}

# 本仓自有包无许可证元数据(非发布物),不参与判定
OWN_PACKAGES = {"kongming-api", "kongming-agent", "liannu"}

# 前端产物随包分发,生产依赖同样只允许宽松许可
NODE_ALLOWED = {"MIT", "ISC", "APACHE-2.0", "BSD-3-CLAUSE", "0BSD", "CC0-1.0"}

_SEPARATORS = (",", ";", "(", ")", " AND ", " OR ", "/")


def _atoms(expr: str) -> list[str]:
    """许可表达式 → 原子名列表(空白归一后按连接符拆分)。"""
    text = f" {expr.upper()} "
    for sep in _SEPARATORS:
        text = text.replace(sep, "\n")
    return [part.strip() for part in text.splitlines() if part.strip()]


def _disallowed(expr: str, allowed: set[str]) -> list[str]:
    return [atom for atom in _atoms(expr) if atom not in allowed]


def _run(argv: list[str], cwd: Path) -> str:
    # 固定命令 + 锁文件中的包名,列表传参不经 shell
    proc = subprocess.run(  # noqa: S603
        argv, cwd=cwd, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        sys.exit(f"命令失败({proc.returncode}): {' '.join(argv)}\n{proc.stderr.strip()}")
    return proc.stdout


def check_python(project: Path) -> list[str]:
    """uv 运行时闭包逐包许可证校验;返回违规描述(空=通过)。"""
    export = _run(
        [
            "uv",
            "export",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--no-hashes",
        ],
        project,
    )
    names = [
        line.split("==", 1)[0].strip()
        for line in export.splitlines()
        if "==" in line and not line.startswith("#")
    ]
    rows = csv.DictReader(
        io.StringIO(_run(["pip-licenses", "-p", *names, "--format=csv"], project))
    )
    problems: list[str] = []
    for row in rows:
        if row["Name"].lower() in OWN_PACKAGES:
            continue
        # 空许可等于未声明:不能按“没有违规原子”放行
        if not _atoms(row["License"]):
            problems.append(f"{row['Name']} {row['Version']}: 未声明许可证")
            continue
        bad = _disallowed(row["License"], PY_ALLOWED)
        if bad:
            problems.append(f"{row['Name']} {row['Version']}: {row['License']} (拒绝 {bad})")
    return problems


def check_node(web: Path) -> list[str]:
    """pnpm 生产依赖按许可证键校验;返回违规描述(空=通过)。"""
    data = json.loads(_run(["pnpm", "licenses", "list", "--prod", "--json"], web))
    problems: list[str] = []
    for license_key, packages in data.items():
        if license_key.upper() in NODE_ALLOWED:
            continue
        names = ", ".join(sorted(str(p.get("name")) for p in packages)[:5])
        problems.append(f"{license_key}: {names}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"python", "node"}:
        print(__doc__)
        return 2
    target = Path(argv[2]).resolve()
    problems = check_python(target) if argv[1] == "python" else check_node(target)
    if problems:
        print(f"许可证门禁失败:{target}")
        for line in problems:
            print(f"  - {line}")
        return 1
    print(f"许可证门禁通过:{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
