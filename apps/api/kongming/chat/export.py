"""问答结果导出为 CSV 或 XLSX。

CSV 使用 UTF-8 BOM、CRLF 行尾和 RFC 4180 转义；XLSX 按表头与前 100 行估算列宽。
输入为调用方已查询并完成权限过滤的列和行，不会重新执行 SQL；行数上限由调用方提供。
"""

from __future__ import annotations

import io
import unicodedata
from typing import cast

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

# RFC 4180:含以下字符的字段须双引号包裹
_CSV_QUOTE_CHARS = (",", '"', "\n", "\r")
# 列宽采样行数与 clamp 边界(中文内容列不至于过窄/超宽列撑爆阅读)
_WIDTH_SAMPLE_ROWS = 100
_WIDTH_MIN = 8
_WIDTH_MAX = 60
# CSV 公式注入中和:文本值以此类字符开头时,Excel 打开会按公式/DDE
# 执行(内容源自业务库,可能他方写入)——前置单引号中和;数值类型不受影响
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell_text(v: object) -> str:
    """CSV/列宽采样共用文本化:None→空串;bool 小写(JSON 语义,非 Python True)。

    两道清洗:XML 非法控制字符剥除(openpyxl 遇之抛
    IllegalCharacterError 裸 500);str 以公式字符开头前置 ' 中和(CSV 注入)。
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v) if isinstance(v, (int, float)) else ILLEGAL_CHARACTERS_RE.sub("", str(v))
    if isinstance(v, str) and s[:1] in _FORMULA_PREFIXES:
        s = "'" + s
    return s


def _cell_native(v: object) -> object:
    """xlsx 单元格类型分流:bool/数值原生(可排序可求和),其余文本化;
    文本同剥 XML 非法控制字符(openpyxl 遇之抛 IllegalCharacterError 裸 500)。"""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return ILLEGAL_CHARACTERS_RE.sub("", str(v))


def _display_width(s: str) -> int:
    """显示宽度:东亚宽字符(W/F)按 2 计,其余按 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def to_csv(columns: list[str], rows: list[list[object]], *, max_rows: int) -> tuple[bytes, bool]:
    """UTF-8 BOM + CRLF 的 CSV;返回 (字节, 是否截断)。"""
    truncated = len(rows) > max_rows

    def _line(cells: list[str]) -> str:
        out: list[str] = []
        for cell in cells:
            quoted = cell
            if any(ch in cell for ch in _CSV_QUOTE_CHARS):
                quoted = '"' + cell.replace('"', '""') + '"'
            out.append(quoted)
        return ",".join(out)

    lines = [_line([_cell_text(c) for c in columns])]
    lines.extend(_line([_cell_text(v) for v in r]) for r in rows[:max_rows])
    # errors="replace":孤立代理(手搓 JSON 可过 json.loads)不至于编码炸 500
    return ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8", errors="replace"), truncated


def to_xlsx(columns: list[str], rows: list[list[object]], *, max_rows: int) -> tuple[bytes, bool]:
    """xlsx(表头加粗 + 自算列宽);返回 (字节, 是否截断)。"""
    truncated = len(rows) > max_rows
    data = rows[:max_rows]

    wb = Workbook()
    ws = cast(Worksheet, wb.active)  # 新建 Workbook 必带活动表(stub 类型为可选)
    ws.title = "查询结果"
    ws.append([_cell_text(c) for c in columns])
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
    for r in data:
        ws.append([_cell_native(v) for v in r])

    for idx, col in enumerate(columns, start=1):
        width = _display_width(_cell_text(col))
        for r in data[:_WIDTH_SAMPLE_ROWS]:
            if idx - 1 < len(r):
                width = max(width, _display_width(_cell_text(r[idx - 1])))
        ws.column_dimensions[get_column_letter(idx)].width = min(
            max(width + 2, _WIDTH_MIN), _WIDTH_MAX
        )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), truncated
