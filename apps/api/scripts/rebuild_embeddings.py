"""从保留的文本重建派生向量;文档块同时按索引文本重算全文 tsv。每批单独提交,
失败时停止但保留已写入的批次;支持 --dry-run 和 --table。"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/ 直跑导入 kongming

from sqlalchemy import text

from kongming.config import get_settings
from kongming.db import get_sessionmaker
from kongming.rag.agent_callback import get_agent_rag_client
from kongming.rag.catalog import vec_literal
from kongming.rag.chunker import index_text
from kongming.rag.models import build_embedding_text
from kongming.redis_client import get_redis

log = logging.getLogger("rebuild_embeddings")

# 表 → 计数/读取 SQL;通用分支另用 update 重建向量,
# document_chunks 走 _apply_chunk_batch(向量+tsv 按层级分治)
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
        "count": "SELECT count(*) FROM rag.document_chunks",
        "select": (
            "SELECT c.id, c.chunk_level, c.content, d.doc_name"
            " FROM rag.document_chunks c JOIN rag.documents d ON d.id = c.document_id"
            " WHERE c.id > :last ORDER BY c.id LIMIT :n"
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
    """行 → embedding 输入文本(与写入侧同源;文档块用文档名+正文索引文本)。"""
    if table == "table_descriptions":
        cols_raw = row.cols or "{}"
        columns = json.loads(cols_raw) if isinstance(cols_raw, str) else cols_raw
        return build_embedding_text(
            row.table_name, row.table_comment or "", columns, alias=row.alias or ""
        )
    if table == "document_chunks":
        return index_text(str(row.doc_name), str(row.content or ""))
    return str(row.content or "")


def _chunk_update_sql(ts_config: str, *, with_vector: bool) -> str:
    """块重建 UPDATE:tsv 恒重算,子块附带 embedding;配置名经白名单校验。"""
    sets = f"tsv = to_tsvector('{ts_config}', :idx)"
    if with_vector:
        sets += ", embedding = CAST(:vec AS halfvec)"
    # S608 豁免:{ts_config} 为白名单校验后的配置名,其余全部绑定参数
    return f"UPDATE rag.document_chunks SET {sets} WHERE id = :id"  # noqa: S608


async def _apply_chunk_batch(session: Any, embedder: Any, rows: Any, *, batch_no: int) -> None:
    """块批:子块重算 embedding 与 tsv,父块仅重算 tsv(不重切块、不动 id)。"""
    ts = get_settings().require_fulltext_ts_config()
    child_rows = [r for r in rows if r.chunk_level == "CHILD"]
    vectors: list[Any] = []
    if child_rows:
        vectors = await embedder.embed([_row_text("document_chunks", r) for r in child_rows])
        if len(vectors) != len(child_rows):
            raise RuntimeError(f"document_chunks 批 {batch_no}: embedding 返回数量不符")
    vi = 0
    for row in rows:
        params: dict[str, Any] = {"id": row.id, "idx": _row_text("document_chunks", row)}
        with_vector = row.chunk_level == "CHILD"
        if with_vector:
            vec = vectors[vi]
            vi += 1
            if not vec:
                raise RuntimeError(f"document_chunks 批 {batch_no}: 行为空向量(id={row.id})")
            params["vec"] = vec_literal(vec)
        await session.execute(text(_chunk_update_sql(ts, with_vector=with_vector)), params)


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
    """重建单表;返回 (总行数, 批次数)。dry-run 只计数不调用不写库。

    document_chunks 覆盖父块与子块:子块重算 embedding+tsv,父块只重算 tsv。"""
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
        if table == "document_chunks":
            await _apply_chunk_batch(session, embedder, rows, batch_no=batch_no)
        else:
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
    parser = argparse.ArgumentParser(
        description="派生数据重建(向量 halfvec 2560;文档块同时重算 tsv)"
    )
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
