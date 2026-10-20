"""全量向量重建(upgrade-rag-qwen3-halfvec-2560,design D4/D5)。

迁移 0058 清空四类派生向量(表说明/历史问句/文档子块/用户记忆)后,本命令
从保留的原文经 Agent embedding 回调(ADR-002:凭证仅在 agent 面)重建
halfvec(2560) 向量:
- 可观测:分批进度日志(表/批次数/行数);
- 按批提交:每批独立事务,失败即止,此前批次已落库;
- 可重跑:重复执行覆盖同一行向量(embedding 缓存命中未变文本,重跑廉价);
- --dry-run:只报分批数量与目标行数,不调用 embedding、不写库;
- --table:按表筛选(缺省四表全量)。

用法:
  uv run python scripts/rebuild_embeddings.py --dry-run
  uv run python scripts/rebuild_embeddings.py --table query_history
  uv run python scripts/rebuild_embeddings.py
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ 直跑导入 kongming

from sqlalchemy import text  # noqa: E402

from kongming.db import get_sessionmaker  # noqa: E402
from kongming.rag.agent_callback import get_agent_rag_client  # noqa: E402
from kongming.rag.models import build_embedding_text  # noqa: E402
from kongming.rag.repository import vec_literal  # noqa: E402
from kongming.redis_client import get_redis  # noqa: E402

log = logging.getLogger("rebuild_embeddings")

# 表 → (计数 SQL, 读取 SQL, 更新 SQL);读取按 id 键集分页(可重跑断点语义)
_TABLES: dict[str, dict[str, str]] = {
    "table_descriptions": {
        "count": "SELECT count(*) FROM rag.table_descriptions",
        "select": (
            "SELECT id, table_name, table_comment,"
            " CAST(columns_comment AS text) AS cols, COALESCE(alias, '') AS alias"
            " FROM rag.table_descriptions WHERE id > :last ORDER BY id LIMIT :n"
        ),
        "update": (
            "UPDATE rag.table_descriptions SET embedding = CAST(:vec AS halfvec) WHERE id = :id"
        ),
    },
    "query_history": {
        "count": "SELECT count(*) FROM rag.query_history",
        "select": (
            "SELECT id, question AS content FROM rag.query_history"
            " WHERE id > :last ORDER BY id LIMIT :n"
        ),
        "update": "UPDATE rag.query_history SET embedding = CAST(:vec AS halfvec) WHERE id = :id",
    },
    "document_chunks": {
        "count": "SELECT count(*) FROM rag.document_chunks WHERE chunk_level = 'CHILD'",
        "select": (
            "SELECT id, content FROM rag.document_chunks WHERE chunk_level = 'CHILD'"
            " AND id > :last ORDER BY id LIMIT :n"
        ),
        "update": (
            "UPDATE rag.document_chunks SET embedding = CAST(:vec AS halfvec) WHERE id = :id"
        ),
    },
    "user_memory": {
        "count": "SELECT count(*) FROM public.user_memory",
        "select": (
            "SELECT id, content FROM public.user_memory WHERE id > :last ORDER BY id LIMIT :n"
        ),
        "update": "UPDATE public.user_memory SET embedding = CAST(:vec AS halfvec) WHERE id = :id",
    },
}


def _row_text(table: str, row: Any) -> str:
    """行 → embedding 输入文本(与写入侧同源:表说明走 build_embedding_text)。"""
    if table == "table_descriptions":
        cols_raw = row.cols or "{}"
        columns = json.loads(cols_raw) if isinstance(cols_raw, str) else cols_raw
        return build_embedding_text(
            row.table_name, row.table_comment or "", columns, alias=row.alias or ""
        )
    return str(row.content or "")


async def _count(session: Any, table: str) -> int:
    return int((await session.execute(text(_TABLES[table]["count"]))).scalar_one())


async def _rebuild_table(
    session: Any,
    embedder: Any,
    table: str,
    *,
    batch_size: int,
    dry_run: bool,
) -> tuple[int, int]:
    """重建单表;返回 (总行数, 批次数)。dry-run 只计数不调用不写库。"""
    spec = _TABLES[table]
    total = await _count(session, table)
    batches = (total + batch_size - 1) // batch_size
    log.info("%s: 目标 %d 行,分 %d 批(每批 %d)", table, total, batches, batch_size)
    if dry_run or total == 0:
        return total, batches
    last = "00000000-0000-0000-0000-000000000000"
    done = 0
    batch_no = 0
    while True:
        rows = (await session.execute(text(spec["select"]), {"last": last, "n": batch_size})).all()
        if not rows:
            break
        batch_no += 1
        texts = [_row_text(table, r) for r in rows]
        vectors = await embedder.embed(texts)
        if len(vectors) != len(rows):
            raise RuntimeError(f"{table} 批 {batch_no}: embedding 返回数量不符")
        for row, vec in zip(rows, vectors, strict=True):
            if not vec:
                raise RuntimeError(f"{table} 批 {batch_no}: 行为空向量(id={row.id})")
            await session.execute(text(spec["update"]), {"id": row.id, "vec": vec_literal(vec)})
        await session.commit()
        done += len(rows)
        last = str(rows[-1].id)
        log.info("%s: 批 %d/%d 完成(累计 %d/%d)", table, batch_no, batches, done, total)
    return total, batches


async def _run(args: argparse.Namespace) -> int:
    tables = args.table or list(_TABLES)
    embedder = get_agent_rag_client(get_redis())
    grand_total = 0
    async with get_sessionmaker()() as session:
        for table in tables:
            total, _ = await _rebuild_table(
                session, embedder, table, batch_size=args.batch_size, dry_run=args.dry_run
            )
            grand_total += total
    mode = "dry-run(未调用未写库)" if args.dry_run else "重建完成"
    log.info("%s:表 %s,合计 %d 行", mode, ",".join(tables), grand_total)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="全量向量重建(halfvec 2560)")
    parser.add_argument("--dry-run", action="store_true", help="只报分批计划,不调用不写库")
    parser.add_argument(
        "--table",
        action="append",
        choices=list(_TABLES),
        help="按表筛选(可重复;缺省四表全量)",
    )
    parser.add_argument("--batch-size", type=int, default=20, help="每批行数(缺省 20)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
