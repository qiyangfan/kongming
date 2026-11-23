"""BM25 关键词排序的文档仓储变体(评测实验)。

只在 KM_RAG_FULLTEXT_RANKER=bm25 的隔离评测 API 进程由
build_document_repo 装入;两轮匹配谓词、候选范围与父块回填沿用基类,
只把关键词排序从 ts_rank 换成 BM25,同分按段落序号。
"""

from typing import Any, cast

from sqlalchemy import text

from kongming.rag.bm25 import bm25_score, idf
from kongming.rag.documents_repo import SqlDocumentRepo


def _group_rows_by_doc(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """候选行按子块聚合:段长取该段任一行,词频按词项并入。"""
    docs: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = docs.setdefault(
            str(row["id"]),
            {
                "id": str(row["id"]),
                "parent_id": row["parent_id"],
                "source_ref": row["source_ref"],
                "seq": int(row["seq"]),
                "doc_len": int(row["dl"] or 0),
                "tf": {},
            },
        )
        entry["tf"][str(row["term"])] = int(row["tf"] or 0)
    return docs


class Bm25DocumentRepo(SqlDocumentRepo):
    """关键词排序改为 BM25 的文档仓储;其余读写行为与基类一致。"""

    async def _children_fulltext_rows(
        self,
        *,
        predicate: str,
        tenant_id: str,
        datasource_key: str,
        question: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """按当前轮谓词对全部匹配子块算 BM25,取前 top_k(同分段落序号升序)。"""
        terms = await self._query_terms(question)
        if not terms:
            return []
        rows = await self._candidate_terms(
            predicate=predicate,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            terms=terms,
        )
        if not rows:
            return []
        n_docs, avg_dl, dfs = await self._scope_stats(tenant_id, datasource_key, terms)
        if n_docs <= 0:
            return []
        term_idf = {term: idf(n_docs, dfs.get(term, 0)) for term in terms}
        scored = [
            (
                bm25_score(
                    term_freqs=entry["tf"],
                    doc_len=entry["doc_len"],
                    avg_doc_len=avg_dl,
                    term_idf=term_idf,
                ),
                entry,
            )
            for entry in _group_rows_by_doc(rows).values()
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1]["seq"], pair[1]["id"]))
        return [
            {
                "id": entry["id"],
                "parent_id": entry["parent_id"],
                "source_ref": entry["source_ref"],
                "score": score,
            }
            for score, entry in scored[:top_k]
        ]

    async def _query_terms(self, question: str) -> list[str]:
        """问句经部署分词配置切出的词项(空问句返回空列表)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        # S608 豁免:{self._ts} 为白名单校验后的配置名
                        f"SELECT tsvector_to_array(to_tsvector('{self._ts}', :q)) AS terms"
                    ),
                    {"q": question},
                )
            )
            .mappings()
            .one()
        )
        terms = cast("list[str]", row["terms"] or [])
        return [str(term) for term in terms]

    async def _candidate_terms(
        self,
        *,
        predicate: str,
        tenant_id: str,
        datasource_key: str,
        question: str,
        terms: list[str],
    ) -> list[dict[str, Any]]:
        """匹配子块 × 问句词项的词频与段长;段长为该段全部词项出现次数之和。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        # S608 豁免:{self._ts} 为白名单校验后的配置名,
                        # 谓词为类内常量形态,其余全部绑定参数
                        "SELECT c.id, c.parent_id, c.source_ref, c.seq,"  # noqa: S608
                        " u.lexeme AS term, cardinality(u.positions) AS tf,"
                        " (SELECT sum(cardinality(p.positions))"
                        "  FROM unnest(c.tsv) AS p(lexeme, positions, weights)) AS dl"
                        " FROM rag.document_chunks c"
                        " CROSS JOIN LATERAL unnest(c.tsv) AS u(lexeme, positions, weights)"
                        " WHERE c.tenant_id=:t AND c.datasource_key=:d"
                        "   AND c.chunk_level='CHILD'"
                        f"{predicate}"
                        "   AND u.lexeme = ANY(CAST(:terms AS text[]))"
                    ),
                    {
                        "t": tenant_id,
                        "d": datasource_key,
                        "q": question,
                        "terms": terms,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def _scope_stats(
        self, tenant_id: str, datasource_key: str, terms: list[str]
    ) -> tuple[int, float, dict[str, int]]:
        """范围内统计:子段落总数 N、平均段长 avgdl、问句词项的段落频率 df。

        统计与候选同一个租户与数据源,范围外资料不参与。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT"
                        " (SELECT count(*) FROM rag.document_chunks"
                        "   WHERE tenant_id=:t AND datasource_key=:d"
                        "     AND chunk_level='CHILD') AS n_docs,"
                        " (SELECT avg(dl) FROM ("
                        "   SELECT coalesce((SELECT sum(cardinality(p.positions))"
                        "     FROM unnest(c.tsv) AS p(lexeme, positions, weights)), 0) AS dl"
                        "   FROM rag.document_chunks c"
                        "   WHERE c.tenant_id=:t AND c.datasource_key=:d"
                        "     AND c.chunk_level='CHILD') z) AS avg_dl"
                    ),
                    {"t": tenant_id, "d": datasource_key},
                )
            )
            .mappings()
            .one()
        )
        df_rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT u.lexeme AS term, count(*) AS df"
                        " FROM rag.document_chunks c"
                        " CROSS JOIN LATERAL unnest(c.tsv) AS u(lexeme, positions, weights)"
                        " WHERE c.tenant_id=:t AND c.datasource_key=:d"
                        "   AND c.chunk_level='CHILD'"
                        "   AND u.lexeme = ANY(CAST(:terms AS text[]))"
                        " GROUP BY u.lexeme"
                    ),
                    {"t": tenant_id, "d": datasource_key, "terms": terms},
                )
            )
            .mappings()
            .all()
        )
        return (
            int(row["n_docs"] or 0),
            float(row["avg_dl"] or 0.0),
            {str(r["term"]): int(r["df"]) for r in df_rows},
        )
