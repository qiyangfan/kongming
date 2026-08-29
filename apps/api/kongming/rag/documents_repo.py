"""document-rag 仓储(024):rag.documents / rag.document_chunks SQL。"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7
from kongming.rag.repository import vec_literal


class SqlDocumentRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()

    async def find_by_name(
        self, *, tenant_id: str, datasource_key: str, doc_name: str
    ) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT id, content_hash FROM rag.documents"
                    " WHERE tenant_id=:t AND datasource_key=:d AND doc_name=:n"
                ),
                {"t": tenant_id, "d": datasource_key, "n": doc_name},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    async def replace_document(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        doc_name: str,
        content_hash: str,
        chunks: list[dict[str, object]],
        vectors: list[list[float] | None],
    ) -> str:
        """同名覆盖式更新(先删后插;FK CASCADE 清旧块);仅 CHILD 写向量/tsv。"""
        existing = await self.find_by_name(
            tenant_id=tenant_id, datasource_key=datasource_key, doc_name=doc_name
        )
        if existing is not None:
            await self._s.execute(
                text("DELETE FROM rag.documents WHERE id=:i"), {"i": str(existing["id"])}
            )
        doc_id = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO rag.documents"
                " (id, tenant_id, datasource_key, doc_name, content_hash, block_count)"
                " VALUES (:i, :t, :d, :n, :h, :bc)"
            ),
            {
                "i": doc_id,
                "t": tenant_id,
                "d": datasource_key,
                "n": doc_name,
                "h": content_hash,
                "bc": len(chunks),
            },
        )
        seq_to_id: dict[int, str] = {}
        for c in chunks:
            cid = str(uuid7())
            seq_to_id[int(str(c["seq"]))] = cid
        vi = 0  # vectors 与 CHILD 块同序
        for c in chunks:
            is_child = c["level"] == "CHILD"
            vec = None
            if is_child:
                vec = vectors[vi] if vi < len(vectors) else None
                vi += 1
            parent_seq = c["parent_seq"]
            await self._s.execute(
                text(
                    "INSERT INTO rag.document_chunks"
                    " (id, document_id, tenant_id, datasource_key, chunk_level,"
                    "  parent_id, seq, source_ref, content, tsv, embedding)"
                    " VALUES (:i, :doc, :t, :d, :lv, :pid, :seq, :ref, :content,"
                    "  to_tsvector('simple', :content), CAST(:vec AS vector))"
                ),
                {
                    "i": seq_to_id[int(str(c["seq"]))],
                    "doc": doc_id,
                    "t": tenant_id,
                    "d": datasource_key,
                    "lv": c["level"],
                    "pid": seq_to_id.get(int(str(parent_seq))) if parent_seq is not None else None,
                    "seq": int(str(c["seq"])),
                    "ref": c["source_ref"],
                    "content": c["content"],
                    "vec": vec_literal(vec) if vec else None,
                },
            )
        return doc_id

    async def list_documents(
        self, *, tenant_id: str, datasource_key: str
    ) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, doc_name, block_count, content_hash, updated_at"
                    " FROM rag.documents WHERE tenant_id=:t AND datasource_key=:d"
                    " ORDER BY updated_at DESC"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def delete_document(self, *, doc_id: str, tenant_id: str) -> bool:
        hit = (
            await self._s.execute(
                text("DELETE FROM rag.documents WHERE id=:i AND tenant_id=:t RETURNING id"),
                {"i": doc_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        return hit is not None

    async def list_chunks(self, *, doc_id: str, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, chunk_level, seq, source_ref, content,"
                    "       embedding IS NOT NULL AS has_embedding"
                    " FROM rag.document_chunks WHERE document_id=:i AND tenant_id=:t"
                    " ORDER BY seq"
                ),
                {"i": doc_id, "t": tenant_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def delete_by_datasource(self, *, tenant_id: str, datasource_key: str) -> None:
        """数据源删除级联(documents 级联 chunks 经 FK CASCADE)。"""
        await self._s.execute(
            text("DELETE FROM rag.documents WHERE tenant_id=:t AND datasource_key=:d"),
            {"t": tenant_id, "d": datasource_key},
        )

    # ── 检索(small-to-big) ──

    async def recall_children_vector(
        self, *, tenant_id: str, datasource_key: str, vector: list[float], top_k: int
    ) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, parent_id, source_ref FROM rag.document_chunks"
                    " WHERE tenant_id=:t AND datasource_key=:d AND chunk_level='CHILD'"
                    "   AND embedding IS NOT NULL"
                    " ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :k"
                ),
                {"t": tenant_id, "d": datasource_key, "vec": vec_literal(vector), "k": top_k},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def recall_children_fulltext(
        self, *, tenant_id: str, datasource_key: str, question: str, top_k: int
    ) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, parent_id, source_ref FROM rag.document_chunks"
                    " WHERE tenant_id=:t AND datasource_key=:d AND chunk_level='CHILD'"
                    "   AND tsv @@ plainto_tsquery('simple', :q)"
                    # R1-M3:相关度排序(009 同款,此前 LIMIT 取任意行)
                    " ORDER BY ts_rank(tsv, plainto_tsquery('simple', :q)) DESC"
                    " LIMIT :k"
                ),
                {"t": tenant_id, "d": datasource_key, "q": question, "k": top_k},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def fetch_parents(
        self, *, tenant_id: str, parent_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not parent_ids:
            return []
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, source_ref, content FROM rag.document_chunks"
                    " WHERE id = ANY(:ids) AND tenant_id=:t AND chunk_level='PARENT'"
                ),
                {"ids": parent_ids, "t": tenant_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]
