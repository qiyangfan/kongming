"""审计归档与重导入测试(audit spec 015 delta,逻辑先行直测)。

run_archive/run_restore 为独立可测函数(session 注入,测试直调):
导出 gzip JSONL+SHA-256 manifest → 库内删除+窗口内保留;同日重跑幂等;
恢复按 id 幂等;manifest 篡改拒导入;归档失败不阻断审计写入。
"""

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.audit.archive import run_archive, run_restore

_NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)


async def _ensure_tenant(s: AsyncSession) -> None:
    """归档测试不依赖种子链,直接幂等补一个租户行(FK 需要)。"""
    from kongming.db import uuid7

    await s.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
            " VALUES (:i, 'default', '默认租户', 'ACTIVE')"
            " ON CONFLICT (tenant_key) DO NOTHING"
        ),
        {"i": str(uuid7())},
    )
    await s.commit()


async def _mk_audit_row(s: AsyncSession, *, days_ago: int, trace_id: str = "trace-x") -> None:
    await _ensure_tenant(s)
    await s.execute(
        text(
            "INSERT INTO public.sys_sql_audit"
            " (id, trace_id, user_id, tenant_id, datasource_key, question,"
            "  sql_text, verdict, created_at)"
            " VALUES (gen_random_uuid(), :tr, NULL,"
            "  (SELECT id FROM public.sys_tenant LIMIT 1), 'default', 'q',"
            "  'SELECT 1', 'PASS', :ca)"
        ),
        {"tr": trace_id, "ca": _NOW - timedelta(days=days_ago)},
    )


async def _mk_oper_row(s: AsyncSession, *, days_ago: int) -> None:
    await s.execute(
        text(
            "INSERT INTO public.sys_oper_log (id, user_id, action, target, detail, ip,"
            " created_at) VALUES (gen_random_uuid(), NULL, 'test.archive', 't', '{}',"
            " '127.0.0.1', :ca)"
        ),
        {"ca": _NOW - timedelta(days=days_ago)},
    )
    await s.commit()


async def _count(s: AsyncSession, table: str, where: str = "") -> int:
    return int(
        (
            await s.execute(text(f"SELECT count(*) FROM public.{table} {where}"))  # noqa: S608  # 测试常量
        ).scalar_one()
    )


async def test_archive_export_delete_and_window_kept(session: AsyncSession, tmp_path: Path) -> None:
    """超窗导出+删除;窗口内(180 天内)保留;manifest 含 SHA-256/行数。"""
    await _mk_audit_row(session, days_ago=200, trace_id="old-a")
    await _mk_audit_row(session, days_ago=10, trace_id="new-a")  # 窗口内
    await _mk_oper_row(session, days_ago=200)
    await _mk_oper_row(session, days_ago=5)  # 窗口内

    result = await run_archive(session, days=180, archive_dir=str(tmp_path), now=_NOW)
    assert result.exported == 2  # 每表 1 条超窗
    assert result.deleted == 2
    assert sorted(result.files) == ["sys_oper_log.jsonl.gz", "sys_sql_audit.jsonl.gz"]
    # 窗口内保留,超窗已删
    assert await _count(session, "sys_sql_audit") == 1
    assert await _count(session, "sys_oper_log") == 1
    # manifest 完整性
    manifest = json.loads(
        (tmp_path / result.window_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["rows"] == 2
    assert len(manifest["files"]) == 2
    # JSONL 可读回,行内容含超窗 trace
    with gzip.open(
        tmp_path / result.window_dir / "sys_sql_audit.jsonl.gz", "rt", encoding="utf-8"
    ) as gz:
        rows = [json.loads(line) for line in gz]
    assert [r["trace_id"] for r in rows] == ["old-a"]


async def test_archive_rerun_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    """同日重跑:manifest 完整→跳过导出(skipped_export)仅补删,不产重复文件。"""
    await _mk_audit_row(session, days_ago=200)
    r1 = await run_archive(session, days=180, archive_dir=str(tmp_path), now=_NOW)
    assert r1.deleted == 1
    r2 = await run_archive(session, days=180, archive_dir=str(tmp_path), now=_NOW)
    assert r2.skipped_export is True
    assert r2.deleted == 0  # 库内已无超窗
    files = list((tmp_path / r1.window_dir).glob("*.jsonl.gz"))
    assert len(files) == 2  # 未重复产出


async def test_restore_idempotent_and_tamper_rejected(
    session: AsyncSession, tmp_path: Path
) -> None:
    """恢复:id 幂等(重复导入跳过);manifest 篡改(SHA-256 不符)拒导入。"""
    await _mk_audit_row(session, days_ago=200, trace_id="restore-me")
    await _mk_oper_row(session, days_ago=200)
    r = await run_archive(session, days=180, archive_dir=str(tmp_path), now=_NOW)
    assert await _count(session, "sys_sql_audit") == 0

    # 恢复:两表全回
    out = await run_restore(session, archive_dir=str(tmp_path), window_dir=r.window_dir)
    assert out.restored == 2 and out.skipped == 0
    assert await _count(session, "sys_sql_audit") == 1
    # 重复恢复:幂等跳过
    out2 = await run_restore(session, archive_dir=str(tmp_path), window_dir=r.window_dir)
    assert out2.restored == 0 and out2.skipped == 2

    # 篡改 gzip 内容后恢复:SHA-256 校验失败拒绝
    gz_path = tmp_path / r.window_dir / "sys_sql_audit.jsonl.gz"
    raw = gz_path.read_bytes()
    gz_path.write_bytes(raw[:-2] + b"x")  # 破坏尾部
    with pytest.raises(ValueError, match="SHA-256"):
        await run_restore(session, archive_dir=str(tmp_path), window_dir=r.window_dir)


async def test_archive_failure_not_blocking_audit_write(
    session: AsyncSession, tmp_path: Path
) -> None:
    """归档失败(目录不可写)不阻断审计写入(audit spec 铁律):run_archive 抛错,
    SqlAuditWriter 照常落库(调用方仅日志,语义直证)。"""
    from kongming.audit.models import SqlAuditEntry
    from kongming.audit.writer import SqlAuditWriter

    await _ensure_tenant(session)

    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")  # mkdir 撞文件 → NotADirectoryError
    with pytest.raises(OSError):
        await run_archive(session, days=180, archive_dir=str(blocker / "sub"), now=_NOW)
    # 审计写入照常(独立短事务,与归档互不影响)
    writer = SqlAuditWriter()
    await writer.record(
        SqlAuditEntry(
            trace_id="post-failure",
            user_id=None,
            tenant_id=str(
                (
                    await session.execute(text("SELECT id FROM public.sys_tenant LIMIT 1"))
                ).scalar_one()
            ),
            datasource_key="default",
            question=None,
            sql_text="SELECT 1",
            verdict="PASS",
        )
    )
    assert await _count(session, "sys_sql_audit", "WHERE trace_id='post-failure'") == 1


# ============ R1 修复直证 ============


async def test_archive_shrink_days_reexports_not_skip(
    session: AsyncSession, tmp_path: Path
) -> None:
    """R1-S2:days 调小后旧 manifest(窗口更早)不覆盖新删除范围——
    不得跳过导出,必须重新导出后再删(否则 90~180 天龄行未导先删)。"""
    await _mk_audit_row(session, days_ago=200)  # 两档窗口都超
    await _mk_audit_row(session, days_ago=120)  # 仅 90 天窗超,180 天窗内
    # 第一轮:180 天窗(只导 200 天龄那条)
    r1 = await run_archive(session, days=180, archive_dir=str(tmp_path), now=_NOW)
    assert r1.exported == 1 and r1.deleted == 1
    # 恢复 200 天龄那条回库,模拟「第一轮后 days 改小」场景
    await run_restore(session, archive_dir=str(tmp_path), window_dir=r1.window_dir)
    assert await _count(session, "sys_sql_audit") == 2
    # 第二轮:同日同目录、manifest 完整,但 cutoff 更晚(90 天窗)——必须重新导出
    r2 = await run_archive(session, days=90, archive_dir=str(tmp_path), now=_NOW)
    assert r2.skipped_export is False  # S2 核心:不跳过
    assert r2.exported == 2  # 重导覆盖,两行全量
    assert r2.deleted == 2
    # 同 days 重跑(第三轮):manifest cutoff == 当前 cutoff,覆盖成立→跳过
    await run_restore(session, archive_dir=str(tmp_path), window_dir=r2.window_dir)
    r3 = await run_archive(session, days=90, archive_dir=str(tmp_path), now=_NOW)
    assert r3.skipped_export is True


async def test_archive_concurrent_serialized(session: AsyncSession, tmp_path: Path) -> None:
    """R1-S3:并发两次 run_archive 进程内锁串行——无交错损坏,manifest 完整。"""
    import asyncio

    await _mk_audit_row(session, days_ago=200, trace_id="cc-a")
    await _mk_oper_row(session, days_ago=200)
    results = await asyncio.gather(
        run_archive(session, days=180, archive_dir=str(tmp_path), now=_NOW),
        run_archive(session, days=180, archive_dir=str(tmp_path), now=_NOW),
    )
    # 串行语义:一次真导出+删除,一次幂等补跑(skipped)
    kinds = sorted([r.skipped_export for r in results])
    assert kinds == [False, True]
    assert sum(r.deleted for r in results) == 2
    # 文件完好:manifest 校验通过(交错损坏会在此暴露)
    wdir = tmp_path / results[0].window_dir
    manifest = json.loads((wdir / "manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        assert _sha256_file(wdir / str(f["name"])) == f["sha256"]


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
