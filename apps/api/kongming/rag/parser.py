"""Markdown 表说明切块解析(上游规则移植;纯函数,零框架依赖)。

协议:`## 表名` 或 `## schema.表名` 二级标题切块;块内按小节边界键
(说明/字段/列/columns/示例/sample,小写匹配)提取三要素。
表名小写折叠(PG 未加引号标识符语义,`## Customer`→customer)。
"""

import re

from kongming.rag.models import EmptyDocument, TableBlock, TableNameTooLong

# `## 表名` / `## schema.表名`(上游同构正则;标题 schema 段不落库——
# schema_name 是请求表单维度,与块内容解耦)
_TABLE_HEADER = re.compile(r"^##\s+(?:([a-zA-Z_][\w]*)\.)?([a-zA-Z_][\w]*)\s*$", re.M)

# 边界键全集(含说明类——`说明: value` 单行形态须剥前缀,上游同款)
_SECTION_KEYS = (
    "字段",
    "列",
    "columns",
    "column",
    "示例",
    "sample",
    "samples",
    "examples",
    "说明",
    "comment",
    "表",
)
_COMMENT_KEYS = ("说明", "comment", "表")
_COLUMNS_KEYS = ("字段", "列", "columns", "column")
_SAMPLE_KEYS = ("示例", "sample", "samples", "examples")

_COL_ITEM_SPLIT = re.compile(r"[,，]")  # 全角逗号同切(中文文档形态)
_COL_NAME_CLEAN = re.compile(r"[^a-zA-Z0-9_]")


def _is_section_start(line: str) -> str | None:
    """行命中任一小节边界键(`key:`/`key:`)则返回该键(小写比对),否则 None。"""
    low = line.strip().lower()
    for key in _SECTION_KEYS:
        if low.startswith(key) and low[len(key) : len(key) + 1] in (":", ":"):
            return key
    return None


def parse_markdown(content: str) -> list[TableBlock]:
    """解析出全部表块(主入口);无任何 `## 表名` 表头 → EmptyDocument。

    同文档同名表块后者覆盖前者(单文档内幂等);表名小写折叠。
    """
    headers = list(_TABLE_HEADER.finditer(content))
    if not headers:
        raise EmptyDocument("请用 `## 表名` 作为二级标题分块")

    seen: dict[str, TableBlock] = {}
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(content)
        table_name = m.group(2).lower()  # PG 未加引号标识符折叠语义
        if len(table_name) > 128:  # 对齐 table_name VARCHAR(128) 列宽,防 DB 异常 500
            raise TableNameTooLong(f"表名超长(>128 字符): {table_name[:16]}…")
        seen[table_name] = _parse_block(content[start:end], table_name)
    return list(seen.values())


class _SectionScan:
    """小节扫描累加器:按小节键收集正文,块结束时合并为三要素。"""

    def __init__(self) -> None:
        self.comment = ""
        self.columns: dict[str, str] = {}
        self.samples: list[str] = []
        self.first_text: list[str] = []

    def flush(self, section: str | None, lines: list[str]) -> None:
        """把一个小节的正文并入对应要素(空正文忽略)。"""
        text = "\n".join(lines).strip()
        if not text:
            return
        if section in _COLUMNS_KEYS:
            self.columns = columns_to_json(text)
        elif section in _SAMPLE_KEYS:
            self.samples = [s.strip() for s in text.split(";") if s.strip()]
        elif section in _COMMENT_KEYS:
            self.comment = _first_value(text)
        elif section is None and not self.comment:
            self.first_text.append(text)


def _parse_block(body: str, table_name: str) -> TableBlock:
    """单表块:按小节扫描提取 说明/字段/示例(上游 section 扫描逻辑同构)。"""
    scan = _SectionScan()
    current: str | None = None
    buf: list[str] = []

    for raw in body.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("#"):  # 更深层标题(###)一律当小节终止
            scan.flush(current, buf)
            buf = []
            current = None
            continue
        key = _is_section_start(line)
        if key is not None:
            scan.flush(current, buf)
            buf = []
            current = key
            inline = _inline_value(line)
            if inline:
                buf.append(inline)
            continue
        if line.strip():
            buf.append(line)
    scan.flush(current, buf)

    comment = scan.comment
    if not comment and scan.first_text:
        # 无「说明:」小节时取第一段非空文本首行(上游 extractFirstLine 语义)
        comment = _first_value(scan.first_text[0])
    return TableBlock(
        table_name=table_name,
        table_comment=comment,
        columns_comment=scan.columns,
        sample_queries=scan.samples,
    )


def _inline_value(line: str) -> str | None:
    """`key: value` 单行形态取冒号后内容(为空则 None,值在后续行)。"""
    pos = _colon_pos(line)
    if pos is None:
        return None
    return line[pos + 1 :].strip() or None


def _colon_pos(line: str) -> int | None:
    """首个冒号位置(半角/全角);无冒号 None。"""
    for i, ch in enumerate(line):
        if ch in (":", ":"):
            return i
    return None


def _first_value(text: str) -> str:
    """多行文本取首行(表说明一句话语义;剩余行属正文不再消费)。"""
    return text.splitlines()[0].strip() if text.strip() else ""


def columns_to_json(text: str) -> dict[str, str]:
    """字段说明文本 → {列名: 说明}(上游 columnsToJson 同构)。

    `[,，]` 切项,每项首空格前=列名(清洗为标识符字符),后=说明;
    全部解析不出时兜底 {"_raw": 整段}(内容不丢)。
    """
    out: dict[str, str] = {}
    for item in _COL_ITEM_SPLIT.split(text):
        item = item.strip()
        if not item:
            continue
        parts = item.split(None, 1)
        name = _COL_NAME_CLEAN.sub("", parts[0])
        if not name:
            continue
        desc = parts[1].strip() if len(parts) > 1 else ""
        out[name] = desc
    return out or {"_raw": text.strip()}
