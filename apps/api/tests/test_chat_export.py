"""覆盖 CSV、XLSX 导出格式、权限、租户隔离和审计。"""
# pyright: reportPrivateUsage=false

import io
from typing import Any, cast

import pytest
from httpx import AsyncClient
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.chat.export import to_csv, to_xlsx
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _user_with_chat_perm

# ============ 纯函数:CSV ============


def test_csv_bom_crlf_and_rfc4180_escaping() -> None:
    content, truncated = to_csv(
        ["name", "note"],
        [["a,b", 'x"y'], ["line\nfeed", None], ["cr\rtail", "plain"]],
        max_rows=100,
    )
    assert truncated is False
    assert content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM(Excel 直开不乱码)
    text = content.decode("utf-8-sig")
    lines = text.split("\r\n")
    assert lines[0] == "name,note"
    assert lines[1] == '"a,b","x""y"'  # 逗号/引号字段包裹,内部引号双写
    assert lines[2] == '"line\nfeed",'  # 换行字段包裹;None → 空串
    assert lines[3] == '"cr\rtail",plain'
    assert text.endswith("\r\n")  # CRLF 行尾


def test_csv_value_type_rendering() -> None:
    content, _ = to_csv(["s", "n", "f", "b", "none"], [["文本", 42, 3.5, True, None]], max_rows=10)
    line = content.decode("utf-8-sig").split("\r\n")[1]
    assert line == "文本,42,3.5,true,"  # bool 小写(JSON 语义);None 空串


def test_csv_truncation() -> None:
    content, truncated = to_csv(["a"], [[1], [2], [3]], max_rows=2)
    assert truncated is True
    lines = [ln for ln in content.decode("utf-8-sig").split("\r\n") if ln]
    assert lines == ["a", "1", "2"]  # 表头 + 截断到上限


# ============ 纯函数:xlsx ============


def test_xlsx_bold_header_widths_and_types() -> None:
    content, truncated = to_xlsx(
        ["名称", "qty", "flag"],
        [["连弩是一种兵器", 30, True], ["木牛流马", 12, False]],
        max_rows=100,
    )
    assert truncated is False
    ws = cast("Any", load_workbook(io.BytesIO(content)).active)
    assert ws.title == "查询结果"
    assert ws["A1"].value == "名称" and ws["A1"].font.bold is True  # 表头加粗
    assert ws["B2"].value == 30 and isinstance(ws["B2"].value, int)  # 数值原生
    assert ws["C2"].value is True  # 布尔原生
    # 自算列宽:中文列(东亚宽字符按 2 计)=「连弩是一种兵器」7 字 ×2 +2 余量
    assert ws.column_dimensions["A"].width is not None
    assert ws.column_dimensions["A"].width == 16
    assert ws.column_dimensions["B"].width >= 8  # clamp 下界


def test_xlsx_truncation() -> None:
    content, truncated = to_xlsx(["a"], [[1], [2], [3]], max_rows=1)
    assert truncated is True
    ws = cast("Any", load_workbook(io.BytesIO(content)).active)
    assert ws.max_row == 2  # 表头 + 1 数据行


def test_xlsx_illegal_control_chars_stripped() -> None:
    """PG text 允许 \\x01 等控制字符;openpyxl 遇之抛 IllegalCharacterError(裸 500)
    ——文本化时剥除,不再 500。"""
    content, _ = to_xlsx(["x"], [["a\x01b\x0bc"]], max_rows=10)
    ws = cast("Any", load_workbook(io.BytesIO(content)).active)
    assert ws["A2"].value == "abc"


def test_csv_formula_injection_neutralized() -> None:
    """CSV 文本值以公式前缀开头时添加单引号；数值类型保持不变。"""
    content, _ = to_csv(
        ["a", "b", "c"],
        [["=HYPERLINK(x)", "@cmd", -30]],
        max_rows=10,
    )
    line = content.decode("utf-8-sig").split("\r\n")[1]
    cells = line.split(",")
    assert cells[0] == "'=HYPERLINK(x)"  # 文本中和
    assert cells[1] == "'@cmd"
    assert cells[2] == "-30"  # 数值类型(负号)不中和


def test_csv_lone_surrogate_no_500() -> None:
    """孤立代理(手搓 JSON 可达)经 errors=replace 替换,不抛 UnicodeEncodeError。"""
    content, _ = to_csv(["a"], [["x\ud800y"]], max_rows=10)
    assert content.startswith(b"\xef\xbb\xbf")  # 正常产出


# ============ 配置面 ============


def test_export_max_rows_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """export_max_rows 下界 ge=1:0 值会让导出恒空,配置错误启动期暴露。"""
    from pydantic import ValidationError

    from kongming.config import Settings

    monkeypatch.setenv("KM_EXPORT_MAX_ROWS", "0")
    with pytest.raises(ValidationError):
        Settings()


# ============ 端点面 ============


async def test_export_unauthenticated_401(client: AsyncClient) -> None:
    r = await client.post("/chat/export", json={"format": "csv", "columns": ["a"], "rows": []})
    assert r.status_code == 401


async def test_export_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from tests.test_chat_query import _bind, _default_tenant_id, _login, _mk_role_with_perms

    await seed_all(session, get_settings())
    uid = await make_user("export-noperm")
    rid = await _mk_role_with_perms(session, "EXPORT_NOCHAT", [])
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, await _default_tenant_id(session))
    token = _login(client)
    r = await client.post(
        "/chat/export",
        json={"format": "csv", "columns": ["a"], "rows": []},
        headers=_auth(token),
    )
    assert r.status_code == 403


async def test_export_invalid_format_422(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """format 传输层枚举收口(Literal):非法值 422,不生成文件。"""
    _, token = await _user_with_chat_perm(session, make_user, "export-fmt")
    r = await client.post(
        "/chat/export",
        json={"format": "pdf", "columns": ["a"], "rows": []},
        headers=_auth(token),
    )
    assert r.status_code == 422


async def test_export_csv_response_shape(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _user_with_chat_perm(session, make_user, "export-csv")
    r = await client.post(
        "/chat/export",
        json={"format": "csv", "columns": ["name", "qty"], "rows": [["连弩", 30]]},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    disposition = r.headers["content-disposition"]
    assert "attachment" in disposition and "filename*=UTF-8''" in disposition
    assert ".csv" in disposition  # 「查询结果-<时间戳>.csv」经 URL 编码
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "连弩" in r.content.decode("utf-8-sig")


async def test_export_xlsx_response_shape(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, token = await _user_with_chat_perm(session, make_user, "export-xlsx")
    r = await client.post(
        "/chat/export",
        json={"format": "xlsx", "columns": ["name"], "rows": [["连弩"]]},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert ".xlsx" in r.headers["content-disposition"]
    ws = cast("Any", load_workbook(io.BytesIO(r.content)).active)
    assert ws["A1"].value == "name" and ws["A2"].value == "连弩"


async def test_export_tenant_forbidden_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """租户白名单复校(与问答端点同构):X-Tenant-Id 指向未绑定租户即 403。"""
    _, token = await _user_with_chat_perm(session, make_user, "export-tenant")
    r = await client.post(
        "/chat/export",
        json={"format": "csv", "columns": ["a"], "rows": []},
        headers={**_auth(token), "X-Tenant-Id": "ghost"},
    )
    assert r.status_code == 403


async def test_export_rows_over_transport_cap_422(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """rows 传输层上限(截断在 JSON 解析之后,挡不住请求体本身的内存
    放大)——超限在解析期即 422;service 层截断保留为双闸。"""
    _, token = await _user_with_chat_perm(session, make_user, "export-cap")
    r = await client.post(
        "/chat/export",
        json={"format": "csv", "columns": ["a"], "rows": [[i] for i in range(10001)]},
        headers=_auth(token),
    )
    assert r.status_code == 422


# ============ 导出审计 ============


async def test_export_audit_logged(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """导出落 sys_oper_log chat.export(fmt/rows/truncated,同事务;数据外发可见性)。"""
    from sqlalchemy import text

    _, token = await _user_with_chat_perm(session, make_user, "export-audit")
    r = await client.post(
        "/chat/export",
        json={"format": "csv", "columns": ["name"], "rows": [["连弩"], ["木牛"]]},
        headers=_auth(token),
    )
    assert r.status_code == 200
    row = (
        (
            await session.execute(
                text(
                    "SELECT action, detail FROM public.sys_oper_log"
                    " WHERE action='chat.export' ORDER BY created_at DESC LIMIT 1"
                )
            )
        )
        .mappings()
        .one()
    )
    assert row["detail"]["fmt"] == "csv"
    assert row["detail"]["rows"] == 2
    assert row["detail"]["truncated"] is False


async def test_export_audit_failure_not_blocking(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """审计写失败不阻断导出(audit spec 铁律,与 SqlAuditWriter 同语义)。"""
    from kongming.auth.repository import SqlOperLogRepo

    async def _boom(self: object, *a: object, **kw: object) -> None:
        raise RuntimeError("oper_log down")

    monkeypatch.setattr(SqlOperLogRepo, "add", _boom)
    _, token = await _user_with_chat_perm(session, make_user, "export-auditfail")
    r = await client.post(
        "/chat/export",
        json={"format": "csv", "columns": ["name"], "rows": [["连弩"]]},
        headers=_auth(token),
    )
    assert r.status_code == 200  # 导出照常
    assert r.content.startswith(b"\xef\xbb\xbf")
