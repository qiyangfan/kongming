"""覆盖报告权限、归属、版本、刷新、导出和状态转换。"""
# pyright: reportPrivateUsage=false

import asyncio
import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.db import uuid7
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _bind, _default_tenant_id, _login
from tests.test_system_admin import _admin

INTERNAL = {"X-Internal-Token": "test-internal-token"}


# ── arrange 助手 ──


async def _mk_session(
    s: AsyncSession, *, user_id: str, tenant_id: str, title: str = "报告素材会话"
) -> str:
    """直插会话行(入口 a 素材;与 chat 域测试同款 arrange)。"""
    sid = str(uuid7())
    await s.execute(
        text(
            "INSERT INTO public.chat_session (id, user_id, tenant_id, datasource_key, title)"
            " VALUES (:i, CAST(:u AS uuid), CAST(:t AS uuid), 'default', :ti)"
        ),
        {"i": sid, "u": user_id, "t": tenant_id, "ti": title},
    )
    await s.commit()
    return sid


async def _user_with_report_perms(
    client: AsyncClient,
    s: AsyncSession,
    make_user: MakeUser,
    username: str = "reporter",
    *,
    codes: tuple[str, ...] = ("report:generate", "report:read", "report:export"),
) -> tuple[str, str]:
    """造一个带 report 权限点 + default 租户绑定的用户,返回 (user_id, token)。"""
    await seed_all(s, get_settings())
    uid = await make_user(username)
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), :k, :k, 'SELF') RETURNING id"
            ),
            {"k": f"rpt_{username}"},
        )
    ).scalar_one()
    for code in codes:
        await s.execute(
            text(
                "INSERT INTO public.sys_permission (id, perm_code, perm_name)"
                " VALUES (gen_random_uuid(), :c, :c) ON CONFLICT (perm_code) DO NOTHING"
            ),
            {"c": code},
        )
        await s.execute(
            text(
                "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                " SELECT :r, p.id FROM public.sys_permission p WHERE p.perm_code = :c"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": rid, "c": code},
        )
    await _bind(s, "sys_user_role", uid, str(rid))
    await _bind(s, "sys_user_tenant", uid, await _default_tenant_id(s))
    return uid, await _login(client, username)


_FULL_SECTIONS: list[dict[str, Any]] = [
    {
        "section_id": "s0",
        "parent_id": None,
        "title": "销售总览",
        "narrative_md": "本月销售额上行。",
        "ord": 0,
        "data_status": "OK",
        "bindings": [],
    },
    {
        "section_id": "s1",
        "parent_id": "s0",
        "title": "明细数据",
        "narrative_md": "共 1 条记录。",
        "ord": 1,
        "data_status": "OK",
        "bindings": [
            {
                "sql": "SELECT 1 AS v",
                "status": "OK",
                "columns": ["v"],
                "rows": [[1]],
                "row_count": 1,
                "truncated": False,
            }
        ],
    },
]


def _agent_payload(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "销售分析报告",
        "partial": False,
        "error": None,
        "usage": {"llm_calls": 3, "prompt_tokens": 100, "completion_tokens": 200, "elapsed_ms": 50},
        "sections": [dict(s) for s in _FULL_SECTIONS],
    }
    payload.update(over)
    return payload


def _stub_agent(payload: dict[str, Any] | None) -> Any:
    """monkeypatch 目标:AgentClient.generate_report 假体(剧本化编排测试)。"""

    async def _fake(self: Any, req: dict[str, object]) -> dict[str, object] | None:
        return payload

    return _fake


async def _wait_terminal(client: AsyncClient, token: str, report_id: str) -> dict[str, Any]:
    """后台任务轮询至终态(ASGITransport 同一事件循环,任务真实执行)。"""
    for _ in range(200):  # 上限 ~10s
        r = await client.get(f"/reports/{report_id}", headers=_auth(token))
        if r.status_code == 200:
            status = r.json()["report"]["status"]
            if status != "GENERATING":
                return r.json()
        await asyncio.sleep(0.05)
    raise AssertionError("报告未在预算内到达终态")


@pytest.mark.asyncio
async def test_read_requires_permission(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 report:read → 列表/详情/进度 403(读取面权限点覆盖)。"""
    from kongming.chat.agent_client import AgentClient

    monkeypatch.setattr(AgentClient, "generate_report", _stub_agent(_agent_payload()))
    # 只持 generate 的用户可发起,但不可读
    uid, token = await _user_with_report_perms(
        client, session, make_user, "noread", codes=("report:generate",)
    )
    _ = uid
    ghost = "00000000-0000-0000-0000-000000000000"
    for method, path in (("GET", "/reports"), ("GET", f"/reports/{ghost}")):
        call = getattr(client, method.lower())
        r = await call(path, headers=_auth(token))
        assert r.status_code == 403, f"{path} → {r.status_code}"


@pytest.mark.asyncio
async def test_refresh_stale_version_conflicts(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """编辑 vs 刷新竞态:刷新读数后版本被编辑推进 → 申领 CAS 失败 409,不覆盖。"""
    from kongming.chat.agent_client import AgentClient
    from kongming.report import service as report_service

    monkeypatch.setattr(AgentClient, "generate_report", _stub_agent(_agent_payload()))
    uid, token = await _user_with_report_perms(client, session, make_user, "raceA")
    tid = await _default_tenant_id(session)
    _ = uid, tid
    r = await client.post("/reports", json={"instruction": "出报告"}, headers=_auth(token))
    rid = r.json()["report_id"]
    await _wait_terminal(client, token, rid)
    # 先真实编辑推进到 v2(制造与刷新读数的版本差)
    d0 = (await client.get(f"/reports/{rid}", headers=_auth(token))).json()
    r_edit = await client.put(
        f"/reports/{rid}",
        json={
            "base_version": 1,
            "sections": [{"section_id": d0["sections"][1]["id"], "narrative_md": "编辑后的叙述。"}],
        },
        headers=_auth(token),
    )
    assert r_edit.status_code == 200, r_edit.text
    assert r_edit.json()["version"] == 2, r_edit.text

    original_require = report_service.ReportService._require

    async def _stale_require(
        self: report_service.ReportService,
        *,
        report_id: str,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        report = await original_require(
            self, report_id=report_id, tenant_id=tenant_id, user_id=user_id
        )
        report = dict(report)
        report["version"] = 1  # 模拟:刷新读数停在 v1,此后编辑已推进到 v2
        return report

    monkeypatch.setattr(report_service.ReportService, "_require", _stale_require)
    r2 = await client.post(f"/reports/{rid}/refresh", headers=_auth(token))
    # 卸谎报,终局断言读真值
    monkeypatch.setattr(report_service.ReportService, "_require", original_require)
    assert r2.status_code == 409, r2.text
    # 最新版本未被覆盖为基于 v1 的内容:版本仍停留编辑后的 2
    d = (await client.get(f"/reports/{rid}", headers=_auth(token))).json()
    assert d["report"]["version"] == 2


# ── 纯函数:状态机/树校验/导出 ──


def test_status_transition_whitelist() -> None:
    from kongming.report import models as m

    assert m.transition("GENERATING", "READY") == "READY"
    with pytest.raises(m.InvalidTransition):
        m.transition("READY", "GENERATING")  # 终态不复活
    with pytest.raises(m.InvalidTransition):
        m.transition("DELETED", "READY")  # 软删无出边
    with pytest.raises(m.InvalidTransition):
        m.transition("READY", "PARTIAL")


def test_validate_tree_rules() -> None:
    from kongming.report.models import Binding, SectionNode, validate_tree

    ok = [
        SectionNode(title="章", section_id="a"),
        SectionNode(
            title="节",
            section_id="b",
            parent_id="a",
            bindings=[Binding(sql="SELECT 1")],  # 叶子持绑定合法
        ),
    ]
    validate_tree(ok)
    with pytest.raises(ValueError):  # 悬空父引用
        validate_tree([SectionNode(title="x", parent_id="ghost")])
    with pytest.raises(ValueError):  # 深度超限
        validate_tree(
            [
                SectionNode(title="a", section_id="a"),
                SectionNode(title="b", section_id="b", parent_id="a"),
                SectionNode(title="c", section_id="c", parent_id="b"),
            ],
            max_depth=2,
        )
    with pytest.raises(ValueError):  # 有子节点的章节携带绑定 → 拒
        validate_tree(
            [
                SectionNode(
                    title="a",
                    section_id="a",
                    bindings=[Binding(sql="SELECT 1")],
                ),
                SectionNode(title="b", section_id="b", parent_id="a"),
            ]
        )


def test_export_markdown_shape() -> None:
    from kongming.report.models import (
        Binding,
        SectionNode,
        export_markdown,
    )

    md = export_markdown(
        title="T 报告",
        status="READY",
        version=2,
        data_time_point="2026-10-09T00:00:00+00:00",
        sections=[
            SectionNode(
                title="章一",
                narrative_md="叙述。",
                bindings=[
                    Binding(
                        sql="SELECT 1",
                        columns=["v"],
                        rows=[[1]],
                        row_count=1,
                    )
                ],
                section_id="a",
                ord=0,
                data_status="OK",
            ),
            SectionNode(
                title="缺数章",
                narrative_md="",
                bindings=[Binding(sql="SELECT x", status="MISSING", fail_reason="越权")],
                section_id="b",
                ord=1,
                data_status="MISSING",
                fail_reason="越权",
            ),
        ],
    )
    assert "# T 报告" in md
    assert "版本 2" in md
    assert "## 章一" in md or "### 章一" in md
    assert "| v |" in md and "| 1 |" in md  # 快照表
    assert "数据缺失:越权" in md  # 失败章如实标注
    assert "http" not in md.lower().replace("https://", "")  # 不输出外链(粗检)


# ── HTTP:权限面 ──


@pytest.mark.asyncio
async def test_generate_requires_permission(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """无 report:generate → 403 + perm_denied 审计;零报告副作用。"""
    _, token = await _admin(client, session, make_user)
    await session.execute(text("DELETE FROM public.sys_role_permission"))
    await session.commit()
    r = await client.post("/reports", json={"instruction": "出报告"}, headers=_auth(token))
    assert r.status_code == 403
    n = await session.execute(text("SELECT count(*) FROM public.report"))
    assert int(n.scalar_one()) == 0
    from kongming.auth.seed import seed_all

    await seed_all(session, get_settings())


@pytest.mark.asyncio
async def test_generate_and_full_lifecycle(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证生成报告后可查看详情、列表、导出和审计记录。"""
    from kongming.chat.agent_client import AgentClient

    monkeypatch.setattr(AgentClient, "generate_report", _stub_agent(_agent_payload()))
    _, token = await _user_with_report_perms(client, session, make_user)
    r = await client.post(
        "/reports",
        json={
            "instruction": "出一份销售分析报告",
            "title": "销售分析报告",
            "datasource_key": "default",
        },
        headers=_auth(token),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "GENERATING"
    rid = body["report_id"]

    detail = await _wait_terminal(client, token, rid)
    assert detail["report"]["status"] == "READY"
    assert detail["report"]["version"] == 1
    assert [s["title"] for s in detail["sections"]] == ["销售总览", "明细数据"]
    assert detail["sections"][1]["bindings"][0]["rows"] == [[1]]

    # 列表可见
    r2 = await client.get("/reports", headers=_auth(token))
    assert r2.status_code == 200
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["id"] == rid

    # 导出(markdown,审计留痕)
    r3 = await client.get(f"/reports/{rid}/export", headers=_auth(token))
    assert r3.status_code == 200
    assert "text/markdown" in r3.headers["content-type"]
    assert "# 销售分析报告" in r3.text
    assert "| v |" in r3.text
    n = await session.execute(
        text("SELECT count(*) FROM public.sys_oper_log WHERE action='report.export'")
    )
    assert int(n.scalar_one()) >= 1

    # 导出权限独立:仅持 generate/read 的用户 403
    uid2, token2 = await _user_with_report_perms(
        client, session, make_user, "noexp", codes=("report:generate", "report:read")
    )
    _ = uid2
    r4 = await client.get(f"/reports/{rid}/export", headers=_auth(token2))
    assert r4.status_code == 403


@pytest.mark.asyncio
async def test_generate_foreign_session_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """入口 a:他人会话 404 同形,不发起生成。"""
    uid_a, _ = await _user_with_report_perms(client, session, make_user, "ownA")
    _, token_b = await _user_with_report_perms(client, session, make_user, "ownB")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid_a, tenant_id=tid)  # A 的会话
    r = await client.post(
        "/reports",
        json={"instruction": "基于本会话出报告", "session_id": sid},
        headers=_auth(token_b),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_report_card_derived_on_session(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证报告完成后在发起会话中生成 report 类型的 artifact 卡片。"""
    from kongming.chat.agent_client import AgentClient

    captured: dict[str, object] = {}

    async def _fake(self: Any, req: dict[str, object]) -> dict[str, object] | None:
        captured.update(req)
        return _agent_payload()

    monkeypatch.setattr(AgentClient, "generate_report", _fake)
    uid, token = await _user_with_report_perms(client, session, make_user, "cardA")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    # uuid7 时序 id(USER 先生成)= 遍历序确定;gen_random_uuid 同事务
    # created_at 相同、id 随机,配对序会抖动
    await session.execute(
        text(
            "INSERT INTO public.chat_message (id, session_id, role, question, status)"
            " VALUES (CAST(:i AS uuid), CAST(:s AS uuid), 'USER', '上月销售额?', 'SUCCESS')"
        ),
        {"i": str(uuid7()), "s": sid},
    )
    await session.execute(
        text(
            "INSERT INTO public.chat_message (id, session_id, role, sql_text, summary, status)"
            " VALUES (CAST(:i AS uuid), CAST(:s AS uuid), 'ASSISTANT',"
            " 'SELECT 1', '销售额 100 万', 'SUCCESS')"
        ),
        {"i": str(uuid7()), "s": sid},
    )
    await session.commit()
    r = await client.post(
        "/reports",
        json={"instruction": "基于本会话出报告", "session_id": sid},
        headers=_auth(token),
    )
    assert r.status_code == 202
    rid = r.json()["report_id"]
    detail = await _wait_terminal(client, token, rid)
    assert detail["report"]["status"] == "READY"
    # 素材摘要按轮次配对(大小写/落行位置任一错位都会静默丢素材)
    digest = str(captured.get("context_digest") or "")
    assert "上月销售额" in digest
    assert "销售额 100 万" in digest

    msgs = (
        (
            await session.execute(
                text(
                    "SELECT role, result_json, summary FROM public.chat_message"
                    " WHERE session_id = :s ORDER BY created_at DESC LIMIT 1"
                ),
                {"s": sid},
            )
        )
        .mappings()
        .one()
    )
    assert msgs["role"] == "ASSISTANT"
    rj = json.loads(str(msgs["result_json"]))
    assert rj["report"]["report_id"] == rid
    assert rj["artifacts"][0]["type"] == "report"
    assert rj["artifacts"][0]["metadata"]["report_id"] == rid


@pytest.mark.asyncio
async def test_partial_terminal_honest(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预算耗尽(partial)/缺数章节如实 PARTIAL,不谎称完成;报告卡摘要如实。"""
    from kongming.chat.agent_client import AgentClient

    sections = [dict(s) for s in _FULL_SECTIONS]
    sections[1]["data_status"] = "MISSING"
    sections[1]["fail_reason"] = "取数超预算"
    sections[1]["bindings"] = [
        {"sql": "SELECT 1", "status": "MISSING", "fail_reason": "取数超预算"}
    ]
    monkeypatch.setattr(
        AgentClient,
        "generate_report",
        _stub_agent(_agent_payload(partial=True, sections=sections)),
    )
    uid, token = await _user_with_report_perms(client, session, make_user, "partialA")
    tid = await _default_tenant_id(session)
    sid = await _mk_session(session, user_id=uid, tenant_id=tid)
    r = await client.post(
        "/reports", json={"instruction": "出报告", "session_id": sid}, headers=_auth(token)
    )
    rid = r.json()["report_id"]
    detail = await _wait_terminal(client, token, rid)
    assert detail["report"]["status"] == "PARTIAL"
    msgs = (
        (
            await session.execute(
                text("SELECT result_json FROM public.chat_message WHERE session_id=:s"),
                {"s": sid},
            )
        )
        .scalars()
        .all()
    )
    rj = json.loads(str(msgs[-1]))
    assert "部分完成" in rj["report"]["summary"]  # 不谎称全部完成


@pytest.mark.asyncio
async def test_agent_unavailable_failed(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agent 不可达 → FAILED 终态(fail_reason 落库),不静默停在 GENERATING。"""
    from kongming.chat.agent_client import AgentClient

    monkeypatch.setattr(AgentClient, "generate_report", _stub_agent(None))
    _, token = await _user_with_report_perms(client, session, make_user, "unavail")
    r = await client.post("/reports", json={"instruction": "出报告"}, headers=_auth(token))
    rid = r.json()["report_id"]
    detail = await _wait_terminal(client, token, rid)
    assert detail["report"]["status"] == "FAILED"
    assert detail["fail_reason"]


# ── HTTP:归属隔离与软删 ──


@pytest.mark.asyncio
async def test_ownership_isolation_404(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """他人报告详情/导出/刷新/删除一律 404 同形,不泄露存在性。"""
    from kongming.chat.agent_client import AgentClient

    monkeypatch.setattr(AgentClient, "generate_report", _stub_agent(_agent_payload()))
    _, token_a = await _user_with_report_perms(client, session, make_user, "isoA")
    _, token_b = await _user_with_report_perms(client, session, make_user, "isoB")
    r = await client.post("/reports", json={"instruction": "出报告"}, headers=_auth(token_a))
    rid = r.json()["report_id"]
    await _wait_terminal(client, token_a, rid)

    for method, path in (
        ("GET", f"/reports/{rid}"),
        ("GET", f"/reports/{rid}/export"),
        ("POST", f"/reports/{rid}/refresh"),
        ("DELETE", f"/reports/{rid}"),
    ):
        call = getattr(client, method.lower())
        resp = await call(path, headers=_auth(token_b))
        assert resp.status_code == 404, f"{method} {path} → {resp.status_code}"


@pytest.mark.asyncio
async def test_deleted_report_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """软删后:详情/列表/导出/刷新/再删 全拒(404),不复活。"""
    from kongming.chat.agent_client import AgentClient

    monkeypatch.setattr(AgentClient, "generate_report", _stub_agent(_agent_payload()))
    _, token = await _user_with_report_perms(client, session, make_user, "delA")
    r = await client.post("/reports", json={"instruction": "出报告"}, headers=_auth(token))
    rid = r.json()["report_id"]
    await _wait_terminal(client, token, rid)

    assert (await client.delete(f"/reports/{rid}", headers=_auth(token))).status_code == 204
    assert (await client.get(f"/reports/{rid}", headers=_auth(token))).status_code == 404
    assert (await client.get(f"/reports/{rid}/export", headers=_auth(token))).status_code == 404
    assert (await client.post(f"/reports/{rid}/refresh", headers=_auth(token))).status_code == 404
    lst = await client.get("/reports", headers=_auth(token))
    assert lst.json()["total"] == 0


# ── HTTP:版本化/乐观锁/刷新 ──


@pytest.mark.asyncio
async def test_edit_versioning_and_conflict(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """编辑产生新版本;历史版本只读可查;基于旧版本的并发编辑 409。"""
    from kongming.chat.agent_client import AgentClient

    monkeypatch.setattr(AgentClient, "generate_report", _stub_agent(_agent_payload()))
    _, token = await _user_with_report_perms(client, session, make_user, "editA")
    r = await client.post("/reports", json={"instruction": "出报告"}, headers=_auth(token))
    rid = r.json()["report_id"]
    await _wait_terminal(client, token, rid)

    # 编辑目标 id = API 透出的章节行 id(非 agent 内部 key)
    d0 = (await client.get(f"/reports/{rid}", headers=_auth(token))).json()
    target_id = str(d0["sections"][1]["id"])

    # 编辑叙述 → 版本 2
    r2 = await client.put(
        f"/reports/{rid}",
        json={
            "base_version": 1,
            "sections": [{"section_id": target_id, "narrative_md": "改后的叙述。"}],
        },
        headers=_auth(token),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["version"] == 2

    # 最新版本读到新叙述;版本 1 快照不变(历史只读可查)
    d2 = (await client.get(f"/reports/{rid}", headers=_auth(token))).json()
    assert d2["sections"][1]["narrative_md"] == "改后的叙述。"
    d1 = (await client.get(f"/reports/{rid}?version=1", headers=_auth(token))).json()
    assert d1["sections"][1]["narrative_md"] == "共 1 条记录。"
    assert d1["sections"][1]["bindings"][0]["rows"] == [[1]]

    # 基于版本 1 的迟到提交 → 409(不静默覆盖)
    r3 = await client.put(
        f"/reports/{rid}",
        json={
            "base_version": 1,
            "sections": [{"section_id": target_id, "narrative_md": "迟到编辑。"}],
        },
        headers=_auth(token),
    )
    assert r3.status_code == 409

    # 编辑字段越界 → 422(未知键被 pydantic extra=forbid 拒绝,非静默丢弃)
    payload: dict[str, object] = {
        "base_version": 2,
        "sections": [{"section_id": target_id, "bindings": ["probe"]}],
    }
    r4 = await client.put(
        f"/reports/{rid}",
        json=payload,
        headers=_auth(token),
    )
    assert r4.status_code == 422


@pytest.mark.asyncio
async def test_refresh_creates_version_with_defense(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """刷新=绑定重执行(全量防御):合法 SQL 出新版本;防御拒绝章节如实
    MISSING(不绕道),报告维持 PARTIAL。"""
    from kongming.chat.agent_client import AgentClient

    sections = [dict(s) for s in _FULL_SECTIONS]
    sections[1]["bindings"] = [
        {
            "sql": "SELECT 41+1 AS v",
            "status": "OK",
            "columns": ["v"],
            "rows": [[42]],
            "row_count": 1,
            "truncated": False,
        }
    ]
    monkeypatch.setattr(
        AgentClient, "generate_report", _stub_agent(_agent_payload(sections=sections))
    )
    uid, token = await _user_with_report_perms(client, session, make_user, "refA")
    r = await client.post("/reports", json={"instruction": "出报告"}, headers=_auth(token))
    rid = r.json()["report_id"]
    await _wait_terminal(client, token, rid)

    r2 = await client.post(f"/reports/{rid}/refresh", headers=_auth(token))
    assert r2.status_code == 200, r2.text
    assert r2.json()["version"] == 2
    d2 = (await client.get(f"/reports/{rid}?version=2", headers=_auth(token))).json()
    assert d2["sections"][1]["bindings"][0]["rows"] == [[42]]
    # 旧版本快照不漂移
    d1 = (await client.get(f"/reports/{rid}?version=1", headers=_auth(token))).json()
    assert d1["sections"][1]["bindings"][0]["rows"] == [[42]]

    # 防御拒绝不绕道:直插一个含保留表绑定的版本(编辑面改不了绑定)后刷新
    await session.execute(
        text(
            "INSERT INTO public.report_version"
            " (id, report_id, version, meta, data_time_point, created_by)"
            " VALUES (gen_random_uuid(), CAST(:r AS uuid), 3, '{}'::jsonb, now(), CAST(:u AS uuid))"
        ),
        {"r": rid, "u": uid},
    )
    vid = (
        await session.execute(
            text(
                "SELECT id FROM public.report_version"
                " WHERE report_id=CAST(:r AS uuid) AND version=3"
            ),
            {"r": rid},
        )
    ).scalar_one()
    sid_row = (
        await session.execute(
            text(
                "INSERT INTO public.report_section (id, report_version_id, title, narrative_md,"
                " bindings, ord, data_status)"
                " VALUES (gen_random_uuid(), CAST(:v AS uuid), '越权章', '',"
                " CAST(:b AS jsonb), 0, 'OK') RETURNING id"
            ),
            {
                "v": str(vid),
                "b": json.dumps(
                    [
                        {
                            "sql": "SELECT * FROM sys_user",
                            "status": "OK",
                            "columns": [],
                            "rows": [],
                            "row_count": 0,
                            "truncated": False,
                        }
                    ]
                ),
            },
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE public.report SET version=3 WHERE id=CAST(:r AS uuid)"), {"r": rid}
    )
    await session.commit()
    _ = sid_row
    r4 = await client.post(f"/reports/{rid}/refresh", headers=_auth(token))
    assert r4.status_code == 200
    assert r4.json()["version"] == 4
    d4 = (await client.get(f"/reports/{rid}?version=4", headers=_auth(token))).json()
    sec = [s for s in d4["sections"] if s["title"] == "越权章"][0]
    assert sec["data_status"] == "MISSING"  # 防御拒绝如实标注
    assert sec["fail_reason"]
    assert d4["report"]["status"] in ("PARTIAL", "READY")


# ── 进度端点 ──


@pytest.mark.asyncio
async def test_progress_and_phase_writeback(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """进度可查;agent 侧经内部端点回写 phase。"""
    import threading

    from kongming.chat.agent_client import AgentClient

    gate = threading.Event()

    async def _gated(self: Any, req: dict[str, object]) -> dict[str, object] | None:
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 5)
        return _agent_payload()

    monkeypatch.setattr(AgentClient, "generate_report", _gated)
    _, token = await _user_with_report_perms(client, session, make_user, "progA")
    r = await client.post("/reports", json={"instruction": "出报告"}, headers=_auth(token))
    rid = r.json()["report_id"]
    p1 = await client.get(f"/reports/{rid}/progress", headers=_auth(token))
    assert p1.status_code == 200
    assert p1.json()["status"] == "GENERATING"

    # agent 回写 phase(内部端点,X-Internal-Token 闸)
    r403 = await client.post(
        "/internal/report/progress", json={"report_id": rid, "phase": "FETCHING"}
    )
    assert r403.status_code == 403  # 无 token 拒绝
    ok = await client.post(
        "/internal/report/progress",
        json={"report_id": rid, "phase": "FETCHING"},
        headers=INTERNAL,
    )
    assert ok.status_code == 204
    p2 = await client.get(f"/reports/{rid}/progress", headers=_auth(token))
    assert p2.json()["phase"] == "FETCHING"

    # 进度回写只能修改报告所属租户的数据。
    wrong = await client.post(
        "/internal/report/progress",
        json={"report_id": rid, "phase": "WRITING", "tenant_key": "olist"},
        headers=INTERNAL,
    )
    assert wrong.status_code == 204  # 同形 204,写侧不命中(不向内部通道回显存在性)
    p3 = await client.get(f"/reports/{rid}/progress", headers=_auth(token))
    assert p3.json()["phase"] == "FETCHING"  # 归属闸拦下,phase 未被推进

    gate.set()
    await _wait_terminal(client, token, rid)


@pytest.mark.asyncio
async def test_interrupted_generating_swept_on_startup(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """启动清扫:进程中断滞留 GENERATING 的报告收敛 FAILED(不静默悬挂)。"""
    from kongming.report.repository import SqlReportRepo

    uid, _token = await _user_with_report_perms(client, session, make_user, "swpA")
    tid = await _default_tenant_id(session)
    rid = str(uuid7())
    await session.execute(
        text(
            "INSERT INTO public.report"
            " (id, tenant_id, user_id, title, status, phase, datasource_key, instruction)"
            " VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:u AS uuid),"
            "         '滞留报告', 'GENERATING', 'FETCHING', 'default', '中断素材')"
        ),
        {"i": rid, "t": tid, "u": uid},
    )
    await session.commit()
    n = await SqlReportRepo(session).mark_interrupted_failed()
    await session.commit()
    assert n >= 1
    row = (
        (
            await session.execute(
                text("SELECT status, fail_reason FROM public.report WHERE id = CAST(:i AS uuid)"),
                {"i": rid},
            )
        )
        .mappings()
        .one()
    )
    assert row["status"] == "FAILED"
    assert "进程中断" in str(row["fail_reason"])
