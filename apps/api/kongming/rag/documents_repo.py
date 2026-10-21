"""document-rag 仓储:rag.documents / rag.document_chunks SQL。"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import DEFAULT_FULLTEXT_TS_CONFIG, validated_fulltext_ts_config
from kongming.db import uuid7
from kongming.rag.repository import vec_literal


class SqlDocumentRepo:
    def __init__(self, s: AsyncSession, ts_config: str = DEFAULT_FULLTEXT_TS_CONFIG) -> None:
        self._s = s
        # 全文分词配置:白名单校验 fail-fast,组合根注入部署值(缺省 simple)。
        # 文档写入(replace_document 的 tsv)与检索(recall_children_fulltext)
        # 共用同一注入值——同库不允许两种分词形态并存
        self._ts = validated_fulltext_ts_config(ts_config)

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()

    async def find_by_name(
        self, *, tenant_id: str, datasource_key: str, doc_name: str
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, content_hash FROM rag.documents"
                        " WHERE tenant_id=:t AND datasource_key=:d AND doc_name=:n"
                    ),
                    {"t": tenant_id, "d": datasource_key, "n": doc_name},
                )
            )
            .mappings()
            .one_or_none()
        )
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
                    # S608 豁免:{self._ts} 为白名单校验后的配置名
                    # (标识符无法绑定参数),其余全部绑定参数
                    "INSERT INTO rag.document_chunks"  # noqa: S608
                    " (id, document_id, tenant_id, datasource_key, chunk_level,"
                    "  parent_id, seq, source_ref, content, tsv, embedding)"
                    " VALUES (:i, :doc, :t, :d, :lv, :pid, :seq, :ref, :content,"
                    f"  to_tsvector('{self._ts}', :content), CAST(:vec AS halfvec))"
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

    async def list_documents(self, *, tenant_id: str, datasource_key: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, doc_name, block_count, content_hash, updated_at"
                        " FROM rag.documents WHERE tenant_id=:t AND datasource_key=:d"
                        " ORDER BY updated_at DESC"
                    ),
                    {"t": tenant_id, "d": datasource_key},
                )
            )
            .mappings()
            .all()
        )
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
            (
                await self._s.execute(
                    text(
                        "SELECT id, chunk_level, seq, source_ref, content,"
                        "       embedding IS NOT NULL AS has_embedding"
                        " FROM rag.document_chunks WHERE document_id=:i AND tenant_id=:t"
                        " ORDER BY seq"
                    ),
                    {"i": doc_id, "t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
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
            (
                await self._s.execute(
                    text(
                        "SELECT id, parent_id, source_ref FROM rag.document_chunks"
                        " WHERE tenant_id=:t AND datasource_key=:d AND chunk_level='CHILD'"
                        "   AND embedding IS NOT NULL"
                        " ORDER BY embedding <=> CAST(:vec AS halfvec) LIMIT :k"
                    ),
                    {"t": tenant_id, "d": datasource_key, "vec": vec_literal(vector), "k": top_k},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    @property
    def _ft_and_pred(self) -> str:
        """AND 轮谓词:全词条匹配。"""
        return f"   AND tsv @@ plainto_tsquery('{self._ts}', :q)"

    @property
    def _ft_or_pred(self) -> str:
        """内容词兜底轮谓词:任一词条命中(词条限 length >= 2,
        滤掉单字虚词);外部文本不进 to_tsquery 语法位。"""
        return (
            "   AND EXISTS (SELECT 1 FROM"  # noqa: S608  # {self._ts} 白名单校验配置名(同上豁免)
            f" unnest(tsvector_to_array(to_tsvector('{self._ts}', :q))) AS tok"
            " WHERE length(tok) >= 2"
            f"   AND tsv @@ plainto_tsquery('{self._ts}', tok))"
        )

    async def _children_fulltext_rows(
        self,
        *,
        predicate: str,
        tenant_id: str,
        datasource_key: str,
        question: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """共享 SELECT 主体(相关度排序;仅匹配谓词随轮次变化)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        # S608 豁免:{self._ts} 为白名单校验后的配置名
                        # (标识符无法绑定参数),谓词为类内常量形态,
                        # 其余全部绑定参数
                        "SELECT id, parent_id, source_ref FROM rag.document_chunks"  # noqa: S608
                        " WHERE tenant_id=:t AND datasource_key=:d AND chunk_level='CHILD'"
                        f"{predicate}"
                        # 相关度排序;分词配置与写入侧同源,
                        # 兜底轮沿用同量纲 ts_rank
                        f" ORDER BY ts_rank(tsv, plainto_tsquery('{self._ts}', :q)) DESC"
                        " LIMIT :k"
                    ),
                    {"t": tenant_id, "d": datasource_key, "q": question, "k": top_k},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def recall_children_fulltext(
        self, *, tenant_id: str, datasource_key: str, question: str, top_k: int
    ) -> list[dict[str, Any]]:
        """查询语义「AND 优先、内容词兜底」：全词条 AND 零命中才落
        内容词(length >= 2)兜底轮,排序/量纲不变;两轮无差别的形态
        被 spec 规范性排除。"""
        for predicate in (self._ft_and_pred, self._ft_or_pred):
            rows = await self._children_fulltext_rows(
                predicate=predicate,
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=question,
                top_k=top_k,
            )
            if rows:
                return rows
        return []

    async def fetch_parents(self, *, tenant_id: str, parent_ids: list[str]) -> list[dict[str, Any]]:
        if not parent_ids:
            return []
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, source_ref, content FROM rag.document_chunks"
                        " WHERE id = ANY(:ids) AND tenant_id=:t AND chunk_level='PARENT'"
                    ),
                    {"ids": parent_ids, "t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def fetch_child_texts(
        self, *, tenant_id: str, datasource_key: str, child_ids: list[str]
    ) -> dict[str, str]:
        """精排候选池子块正文批量取回(启用精排时才调用,
        未启用路径零成本)。返回 {child_id: content};已删块自然缺席。"""
        if not child_ids:
            return {}
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, content FROM rag.document_chunks"
                        " WHERE id = ANY(:ids) AND tenant_id=:t AND datasource_key=:d"
                        "   AND chunk_level='CHILD'"
                    ),
                    {"ids": child_ids, "t": tenant_id, "d": datasource_key},
                )
            )
            .mappings()
            .all()
        )
        return {str(r["id"]): str(r["content"]) for r in rows}
