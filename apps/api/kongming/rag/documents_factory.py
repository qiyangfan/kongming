"""文档仓储组合根:按部署配置选择关键词排序实现。

BM25 只在 KM_RAG_FULLTEXT_RANKER=bm25 的隔离评测 API 进程装入,
生产部署走缺省 ts_rank,行为不变。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.config import get_settings
from kongming.rag.documents_repo import SqlDocumentRepo


def build_document_repo(session: AsyncSession) -> SqlDocumentRepo:
    """按部署配置构建文档仓储:缺省 ts_rank;评测实验进程可换 BM25 排序。"""
    settings = get_settings()
    if settings.require_fulltext_ranker() == "bm25":
        from kongming.rag.documents_bm25 import Bm25DocumentRepo

        return Bm25DocumentRepo(session, ts_config=settings.rag_fulltext_ts_config)
    return SqlDocumentRepo(session, ts_config=settings.rag_fulltext_ts_config)
