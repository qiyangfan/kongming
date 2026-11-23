"""块索引上下文:embedding 与 tsv 用「文档名 + 块正文」,content 列保持原文。

判别语料(正文剥离店名)证明:块被索引文本缺文档名时,按文档名提问对所有
召回路径无解;这里锁定入库装配——子块 embedding 载荷含文档名前缀,全文 tsv
含文档名词项,而返回给模型/引用的 content 不含前缀。
"""
# pyright: reportPrivateUsage=false

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.rag.chunker import chunk_document, index_text
from kongming.rag.documents_repo import SqlDocumentRepo
from kongming.rag.documents_service import upload_document

DOC = (
    "# 营业与押金\n\n本店营业时间为每日 09:00 至 21:00。\n\n"
    "# 会员与折扣\n\n本店联名卡卡号前缀为 LY-SX-01。\n"
)
DOC_NAME = "storex manual.md"


class _FakeEmbedder:
    """记录入库 embedding 载荷的假向量端口(2560 维与部署模型一致)。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.25] * 2560 for _ in texts]


async def _tenant(session: AsyncSession, key: str) -> str:
    row = await session.execute(
        text(
            "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
            " VALUES (gen_random_uuid(), :k, :k, 'ACTIVE') RETURNING id"
        ),
        {"k": key},
    )
    await session.commit()
    return str(row.scalar_one())


def test_index_text_is_doc_name_plus_original_content() -> None:
    """纯函数:前缀为文档名,正文逐字保留为后缀。"""
    assert index_text("a.md", "正文") == "a.md\n正文"
    assert index_text(DOC_NAME, DOC).endswith(DOC)


async def test_upload_embeds_index_text_and_keeps_content(session: AsyncSession) -> None:
    """上传:子块 embedding 载荷=文档名+正文;tsv 含文档名;content 原文。"""
    tid = await _tenant(session, "cx")
    repo = SqlDocumentRepo(session)
    embedder = _FakeEmbedder()
    doc_id, _, skipped = await upload_document(
        repo,
        embedder,
        tenant_id=tid,
        datasource_key="default",
        doc_name=DOC_NAME,
        text=DOC,
    )
    assert skipped is False
    children = [str(c["content"]) for c in chunk_document(DOC_NAME, DOC) if c["level"] == "CHILD"]
    assert embedder.calls == [[index_text(DOC_NAME, c) for c in children]]

    rows = (
        await session.execute(
            text(
                "SELECT chunk_level, content, embedding IS NOT NULL AS has_vec"
                " FROM rag.document_chunks WHERE document_id=:d ORDER BY seq"
            ),
            {"d": doc_id},
        )
    ).all()
    assert any(r.chunk_level == "PARENT" for r in rows)
    for row in rows:
        assert DOC_NAME not in str(row.content)  # 正文原文,不含文档名前缀
        assert (row.chunk_level == "CHILD") == row.has_vec  # 父块不背向量
    matched = (
        await session.execute(
            text(
                "SELECT count(*) FROM rag.document_chunks"
                " WHERE document_id=:d AND tsv @@ plainto_tsquery('simple', 'storex')"
            ),
            {"d": doc_id},
        )
    ).scalar_one()
    assert matched == len(rows)  # 全文索引含文档名词项
