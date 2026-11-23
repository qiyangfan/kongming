"""文档父子分块器(document-rag spec):纯函数,零依赖。

父块 ~800 字符按标题层级(Markdown 标题行聚合;无标题按段落/长度兜底);
子块 ~200 字符滑窗(步进 =overlap 50);source_ref 定位
`{文档名} §{父块序号}{父块标题前 20 字}`。
"""

import re

PARENT_SOFT_MAX = 800
CHILD_SIZE = 200
CHILD_STEP = 150  # overlap = SIZE - STEP = 50

_HEADING = re.compile(r"^#{1,6}\s+", re.M)


class EmptyDocument(Exception):
    """全文空白(无有效内容)。"""


class TooManyChunks(Exception):
    """块数超上限(防大文档切块风暴)。"""


MAX_PARENT_BLOCKS = 200


def _heading_sections(text: str) -> list[tuple[str, list[str]]]:
    """按标题行切段;返回 [(标题前 20 字, 段内各行)]。"""
    sections: list[tuple[str, list[str]]] = []
    cur_title = ""
    cur: list[str] = []
    for line in text.split("\n"):
        if _HEADING.match(line):
            if cur:
                sections.append((cur_title, cur))
            cur_title = line.lstrip("#").strip()[:20]
            cur = [line]
        else:
            cur.append(line)
    if cur:
        sections.append((cur_title, cur))
    return sections


def _parent_cut(block: str) -> int:
    """超软上限处的切点:优先句号,其次换行,都过短则定长切。"""
    cut = block.rfind("。", 0, PARENT_SOFT_MAX)
    if 0 < cut < PARENT_SOFT_MAX // 2:
        cut = block.rfind("\n", 0, PARENT_SOFT_MAX)
    if cut <= 0 or cut < PARENT_SOFT_MAX // 2:
        return PARENT_SOFT_MAX
    return cut + 1  # 切割含句号(下块不以「。」开头)


def _split_long_block(title: str, block: str) -> list[tuple[str, str]]:
    """单个超软上限的段按段落边界续切,返回若干 (标题, 父块)。"""
    parents: list[tuple[str, str]] = []
    while len(block) > PARENT_SOFT_MAX:
        cut = _parent_cut(block)
        parents.append((title, block[:cut].strip()))
        block = block[cut:].strip()
    if block:
        parents.append((title, block))
    return parents


def _split_parents(text: str) -> list[tuple[str, str]]:
    """按标题层级聚合父块;返回 [(标题前 20 字, 父块文本)]。"""
    parents: list[tuple[str, str]] = []
    for title, sec_lines in _heading_sections(text):
        block = "\n".join(sec_lines).strip()
        if block:
            parents.extend(_split_long_block(title, block))
    return parents


def _split_children(parent_text: str, *, sentence_boundary: bool = False) -> list[str]:
    """子块滑窗(定长步进,overlap 兜底语义连续性)。

    句界校准档:窗口右缘优先对齐句界(回退不越半窗,单句超长才句中截断),
    步进=块长-固定 overlap(短块不足 overlap 时无重叠推进),尾段不丢。"""
    out: list[str] = []
    if sentence_boundary:
        i = 0
        n = len(parent_text)
        while i < n:
            ideal_end = min(i + CHILD_SIZE, n)
            end = _sentence_cut(parent_text, start=i, ideal_end=ideal_end)
            out.append(parent_text[i:end])
            if end >= n:
                break
            nxt = end - (CHILD_SIZE - CHILD_STEP)  # 固定 overlap=50
            i = nxt if nxt > i else end
        return out
    i = 0
    while i < len(parent_text):
        tail = parent_text[i : i + CHILD_SIZE]
        # 尾块短于 overlap 步进余量时不再产块(原块滑窗天然含该尾段)
        if out and len(tail) <= (CHILD_SIZE - CHILD_STEP):
            break
        out.append(tail)
        i += CHILD_STEP
    return out


_SENTENCE_ENDS = ("。", "!", "?", ";", "\n")


def _sentence_cut(text: str, *, start: int, ideal_end: int) -> int:
    """窗口右缘回退至最近句界(含句界字符);无句界或越半窗则定长截断。

    回退下限=起点+半窗(保子块长度下限,防碎片块)。纯函数。"""
    ideal_end = min(ideal_end, len(text))
    floor = start + CHILD_SIZE // 2
    best = -1
    for ch in _SENTENCE_ENDS:
        pos = text.rfind(ch, floor, ideal_end)
        if pos > best:
            best = pos
    if best < floor:
        return ideal_end
    return best + 1


def index_text(doc_name: str, content: str) -> str:
    """块的被索引文本=文档名 + 换行 + 块正文(embedding 与全文 tsv 共用)。"""
    return f"{doc_name}\n{content}"


def chunk_document(
    doc_name: str, text: str, *, sentence_boundary: bool = False
) -> list[dict[str, object]]:
    """文档 → 父子块列表(扁平:PARENT 行与 CHILD 行按序,CHILD 带 parent 序号)。

    返回 [{level, seq, parent_seq(None 对 PARENT), content, source_ref}];
    parent_seq 为父块在返回列表中的 seq,供落库时映射 parent_id。
    sentence_boundary=True 启用子块句界校准(部署级可选档,缺省关=既有
    切分逐字一致)。
    """
    if not text.strip():
        raise EmptyDocument(doc_name)
    parents = _split_parents(text)
    if len(parents) > MAX_PARENT_BLOCKS:
        raise TooManyChunks(f"父块数超上限({MAX_PARENT_BLOCKS})")
    out: list[dict[str, object]] = []
    seq = 0
    for p_idx, (title, ptext) in enumerate(parents, start=1):
        parent_seq = seq
        out.append(
            {
                "level": "PARENT",
                "seq": parent_seq,
                "parent_seq": None,
                "content": ptext,
                "source_ref": f"{doc_name} §{p_idx}{title}",
            }
        )
        seq += 1
        for child in _split_children(ptext, sentence_boundary=sentence_boundary):
            out.append(
                {
                    "level": "CHILD",
                    "seq": seq,
                    "parent_seq": parent_seq,
                    "content": child,
                    "source_ref": f"{doc_name} §{p_idx}{title}",
                }
            )
            seq += 1
    return out
