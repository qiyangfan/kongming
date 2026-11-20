"""审计留存归档(audit spec):超窗记录导出 gzip JSONL + SHA-256 manifest
后库内删除;重导入按 id 幂等。

逻辑全部在独立可测函数(调度器 lifespan 薄壳/管理端点仅转调):
- 窗口目录名=导出日(同日重跑幂等:manifest 完整则跳过导出直接补删);
- 导出与删除分步:manifest 写盘成功才删,删除失败保留文件(下轮重删);
- 失败仅记 error 日志,不阻断审计写入。
"""

import asyncio
import gzip
import hashlib
import json
import logging
import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_TABLES = ("sys_sql_audit", "sys_oper_log")  # 两审计表,导出文件基名=表名
_FETCH_BATCH = 5000
# 归档互斥:手动端点与每日调度共用入口,并发跑同窗口目录会
# gzip "wt" 截断交错致文件损坏且删除后不可恢复——进程内锁序列化
# (单 worker 前提;多 worker 需 PG advisory lock)
_archive_lock = asyncio.Lock()


@dataclass(frozen=True)
class ArchiveResult:
    exported: int  # 导出总行数
    deleted: int  # 库内删除总行数
    files: list[str]  # 产出文件名(相对窗口目录)
    window_dir: str  # 窗口目录名(导出日)
    skipped_export: bool = False  # manifest 已完整,重跑跳过导出(幂等)


@dataclass(frozen=True)
class RestoreResult:
    restored: int  # 实际恢复行数
    skipped: int  # 幂等跳过(已存在)行数


def _window_dir(archive_dir: str, now: datetime) -> Path:
    return Path(archive_dir) / now.strftime("%Y%m%d")


async def _export_table(session: AsyncSession, table: str, cutoff: datetime, path: Path) -> int:
    """分批导出单表超窗记录为 gzip JSONL 并返回行数。

    SHA-256 必须基于压缩后的落盘文件计算。
    """
    rows = 0
    with gzip.open(path, "wt", encoding="utf-8") as gz:
        offset = 0
        while True:
            batch = (
                (
                    await session.execute(
                        text(
                            f"SELECT * FROM public.{table}"  # noqa: S608  # 常量表名(内部两表)
                            " WHERE created_at < :cutoff ORDER BY created_at ASC, id ASC"
                            " LIMIT :lim OFFSET :off"
                        ),
                        {"cutoff": cutoff, "lim": _FETCH_BATCH, "off": offset},
                    )
                )
                .mappings()
                .all()
            )
            if not batch:
                break
            for r in batch:
                gz.write(json.dumps(dict(r), ensure_ascii=False, default=str) + "\n")
                rows += 1
            offset += len(batch)
            if len(batch) < _FETCH_BATCH:
                break
    return rows


async def run_archive(
    session: AsyncSession, *, days: int, archive_dir: str, now: datetime | None = None
) -> ArchiveResult:
    """超窗(早于 now-days)记录归档:导出两表 gzip JSONL + manifest(SHA-256)
    → 库内删除。同日重跑幂等(manifest 完整且窗口覆盖则跳过导出仅补删);
    全程持进程锁(并发交错=文件损坏且删除后不可恢复)。"""
    async with _archive_lock:
        return await _run_archive_locked(session, days=days, archive_dir=archive_dir, now=now)


async def _run_archive_locked(
    session: AsyncSession, *, days: int, archive_dir: str, now: datetime | None
) -> ArchiveResult:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    wdir = _window_dir(archive_dir, now)
    manifest_path = wdir / "manifest.json"

    skipped_export = False
    files: list[str] = []
    exported = 0

    if manifest_path.exists():
        reused = _load_reusable_manifest(wdir, manifest_path, cutoff)
        if reused is not None:
            skipped_export = True
            files = [str(f["name"]) for f in reused["files"]]
            exported = int(reused.get("rows", 0))

    if not skipped_export:
        wdir.mkdir(parents=True, exist_ok=True)
        manifest, exported, files = await _export_all(session, wdir, cutoff, now)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # manifest 写盘成功(或已完整)才删除;删除失败保留文件(下轮重删)
    deleted = await _delete_all(session, cutoff)
    await session.commit()
    return ArchiveResult(
        exported=exported,
        deleted=deleted,
        files=files,
        window_dir=wdir.name,
        skipped_export=skipped_export,
    )


def _load_reusable_manifest(
    wdir: Path, manifest_path: Path, cutoff: datetime
) -> dict[str, Any] | None:
    """读同日 manifest;完整且 SHA-256 校验通过、cutoff 覆盖当前范围时返回它,否则 None。

    manifest_cutoff 必须覆盖当前清理范围;导出文件包含 created_at < manifest_cutoff 的行,
    其中必须包含当前清理将删除的全部记录。清理天数减少时旧 manifest 可能缺少新增区间,不能复用。
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ok = (
            all(
                (wdir / f["name"]).exists() and _sha256_file(wdir / f["name"]) == f["sha256"]
                for f in manifest.get("files", [])
            )
            and len(manifest.get("files", [])) == len(_TABLES)
            and datetime.fromisoformat(str(manifest["cutoff"])) >= cutoff
        )
        if ok:
            return cast("dict[str, Any]", manifest)
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("归档 manifest 损坏,重新导出: %s", manifest_path)
    return None


async def _export_all(
    session: AsyncSession, wdir: Path, cutoff: datetime, now: datetime
) -> tuple[dict[str, object], int, list[str]]:
    """两表逐张导出并写 manifest 内容;返回 (manifest, 总行数, 文件名列表)。"""
    manifest_files: list[dict[str, object]] = []
    exported = 0
    files: list[str] = []
    for table in _TABLES:
        path = wdir / f"{table}.jsonl.gz"
        rows = await _export_table(session, table, cutoff, path)
        manifest_files.append({"name": path.name, "sha256": _sha256_file(path), "rows": rows})
        exported += rows
        files.append(path.name)
    manifest: dict[str, object] = {
        "version": 1,
        "exported_at": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "rows": exported,
        "files": manifest_files,
    }
    return manifest, exported, files


async def _delete_all(session: AsyncSession, cutoff: datetime) -> int:
    """按 cutoff 删除两表超窗记录,返回库内删除总行数。"""
    deleted = 0
    for table in _TABLES:
        res = cast(
            "CursorResult[Any]",
            await session.execute(
                text(f"DELETE FROM public.{table} WHERE created_at < :cutoff"),  # noqa: S608  # 常量表名
                {"cutoff": cutoff},
            ),
        )
        deleted += res.rowcount
    return deleted


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def run_restore(session: AsyncSession, *, archive_dir: str, window_dir: str) -> RestoreResult:
    """重导入:manifest SHA-256 逐文件校验(不符即拒)→ 逐行 INSERT
    ON CONFLICT DO NOTHING(id 幂等)。window_dir 须为合法目录名(端点已收口)。
    值侧按 information_schema 列类型做客户端转换(asyncpg 按 CAST 声明在
    客户端预检 codec,绑 str 到 timestamptz/uuid 位置直接 TypeError——
    CAST 救不了参数编码,必须值转换)。"""
    wdir = Path(archive_dir) / window_dir
    manifest = json.loads((wdir / "manifest.json").read_text(encoding="utf-8"))
    col_types = await _column_types(session)
    restored = 0
    skipped = 0
    for f in manifest["files"]:
        path = wdir / str(f["name"])
        if _sha256_file(path) != f["sha256"]:
            raise ValueError(f"SHA-256 校验失败: {f['name']}")
        table = str(f["name"]).removesuffix(".jsonl.gz")
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            for line in gz:
                if await _restore_row(session, table, json.loads(line), col_types):
                    restored += 1
                else:
                    skipped += 1
    await session.commit()
    return RestoreResult(restored=restored, skipped=skipped)


async def _restore_row(
    session: AsyncSession, table: str, row: dict[str, object], col_types: dict[tuple[str, str], str]
) -> bool:
    """插入单行(ON CONFLICT DO NOTHING);实际插入返回 True,幂等跳过返回 False。"""
    params = {c: _coerce(v, col_types.get((table, c), "text")) for c, v in row.items()}
    cols = list(params)
    inserted = (
        await session.execute(
            text(
                f"INSERT INTO public.{table} ({', '.join(cols)})"  # noqa: S608  # 列名来自本服务导出的归档
                f" VALUES ({', '.join(':' + c for c in cols)})"
                " ON CONFLICT (id) DO NOTHING RETURNING id"
            ),
            params,
        )
    ).scalar_one_or_none()
    return inserted is not None


def _coerce(value: object, data_type: str) -> object:
    """JSON 读回值 → asyncpg 客户端 codec 期望的 Python 类型(None 原样)。"""
    if value is None:
        return None
    if data_type == "uuid":
        return _uuid.UUID(str(value))
    if data_type.startswith("timestamp") or data_type == "date":
        return datetime.fromisoformat(str(value))
    if data_type in ("integer", "bigint", "smallint"):
        return int(str(value))
    # asyncpg 对 jsonb 参数要求 JSON 文本(str),不收 dict;字符串原样绑定
    if data_type == "jsonb" and isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


async def _column_types(session: AsyncSession) -> dict[tuple[str, str], str]:
    """两审计表的 (表名, 列名) → PG data_type 映射(恢复类型转换用,一次内省)。"""
    rows = (
        await session.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns"
                " WHERE table_schema='public' AND table_name = ANY(CAST(:ts AS text[]))"
            ),
            {"ts": list(_TABLES)},
        )
    ).all()
    return {(r.table_name, r.column_name): r.data_type for r in rows}
