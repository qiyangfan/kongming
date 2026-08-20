"""文档文件解析为纯文本(009):PDF/Word 扩展格式,解析后复用 Markdown 切块链路。

选型(design D7):pypdf(纯 Python,BSD)+ python-docx(MIT,仅 .docx);
旧 .doc 二进制格式不支持(Python 生态无维护良好的纯库,提案 Non-goal)。
解析为同步 CPU 密集操作——调用方须在工作线程执行(asyncio.to_thread)。
"""

import io
import logging

from kongming.rag.models import UnsupportedFileType

log = logging.getLogger(__name__)

TEXT_EXTENSIONS = frozenset({".md", ".markdown", ".txt"})
BINARY_EXTENSIONS = frozenset({".pdf", ".docx"})


def supported_extension(name: str) -> str | None:
    """文件名 → 小写扩展名;不支持的扩展名返回 None(端点 400)。"""
    lower = name.lower()
    for ext in TEXT_EXTENSIONS | BINARY_EXTENSIONS:
        if lower.endswith(ext):
            return ext
    return None


def parse_to_text(content: bytes, filename: str) -> str:
    """文件字节 → 纯文本(在工作线程内调用;解析失败/空文本抛 UnsupportedFileType)。

    解析失败与「内容为空」同口径 400:两者都无法产出可用表块,不写入知识库。
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        text = _parse_pdf(content)
    elif lower.endswith(".docx"):
        text = _parse_docx(content)
    else:
        # 文本类(md/markdown/txt):utf-8 解码失败按解析失败处理
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as e:
            raise UnsupportedFileType("文本文件解码失败(须为 UTF-8)") from e
    if not text.strip():
        raise UnsupportedFileType("文件解析后无文本内容(扫描件/空文档?)")
    return text


def _parse_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - 依赖缺失属部署错误
        raise UnsupportedFileType("pypdf 未安装") from e
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as e:  # pypdf 异常族宽:加密/损坏/畸形结构统一 400
        log.info("PDF 解析失败: %s", e)
        raise UnsupportedFileType(f"PDF 解析失败:{e.__class__.__name__}") from e
    return "\n".join(pages)


def _parse_docx(content: bytes) -> str:
    try:
        import docx
    except ImportError as e:  # pragma: no cover - 依赖缺失属部署错误
        raise UnsupportedFileType("python-docx 未安装") from e
    try:
        document = docx.Document(io.BytesIO(content))
        paragraphs = [p.text for p in document.paragraphs]
    except Exception as e:
        log.info("docx 解析失败: %s", e)
        raise UnsupportedFileType(f"docx 解析失败:{e.__class__.__name__}") from e
    return "\n".join(paragraphs)
