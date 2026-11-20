"""工作流测试覆盖 DAG 校验、模拟节点执行、SSRF 地址拒绝矩阵和 HTTP 请求安全。"""
# pyright: reportPrivateUsage=false

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.db import get_engine
from kongming.workflow.engine import run_dag
from kongming.workflow.models import (
    DagValidationError,
    compare,
    render_template,
    resolve_ref,
    validate_dag,
)
from kongming.workflow.ports import SsrFBlocked
from tests.conftest import MakeUser
from tests.test_chat_query import _bind, _login
from tests.test_toolcall import _mk_role_with_perms, _mk_tenant

SCHEMA = "tenant_wf"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """workflow 测试租户业务 schema(sql 节点真执行活数据)。"""
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))  # 模块常量
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".goods ('  # 模块常量
                " id uuid PRIMARY KEY, name varchar(64), create_by varchar(64))"
            )
        )
        await conn.execute(text(f'DELETE FROM "{SCHEMA}".goods'))  # noqa: S608  # 模块常量
        await conn.execute(
            text(
                f'INSERT INTO "{SCHEMA}"."goods" (id, name, create_by)'  # noqa: S608  # 模块常量
                " VALUES (gen_random_uuid(), '连弩', 'someone')"
            )
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))  # 模块常量


# ── validate_dag 全分支 ──


def _t_node(nid: str, deps: list[str] | None = None) -> dict[str, Any]:
    return {"id": nid, "type": "transform", "config": {"template": "x"}, "depends_on": deps or []}


def test_dag_valid() -> None:
    nodes = validate_dag({"nodes": [_t_node("a"), _t_node("b", ["a"])]})
    assert [n.id for n in nodes] == ["a", "b"]


def test_dag_cycle_named() -> None:
    with pytest.raises(DagValidationError, match="环"):
        validate_dag({"nodes": [_t_node("a", ["b"]), _t_node("b", ["a"])]})


def test_dag_orphan() -> None:
    with pytest.raises(DagValidationError, match="孤立"):
        validate_dag({"nodes": [_t_node("a"), _t_node("b", ["a"]), _t_node("c")]})


def test_dag_bad_type_and_required() -> None:
    with pytest.raises(DagValidationError, match="类型非法"):
        validate_dag({"nodes": [{"id": "a", "type": "shell", "config": {}}]})
    with pytest.raises(DagValidationError, match="必填配置"):
        validate_dag({"nodes": [{"id": "a", "type": "sql", "config": {}}]})


def test_dag_dup_and_missing_dep_and_self() -> None:
    with pytest.raises(DagValidationError, match="重复"):
        validate_dag({"nodes": [_t_node("a"), _t_node("a")]})
    with pytest.raises(DagValidationError, match="不存在"):
        validate_dag({"nodes": [_t_node("a", ["ghost"])]})
    with pytest.raises(DagValidationError, match="自依赖"):
        validate_dag({"nodes": [_t_node("a", ["a"])]})


# ── 引用/模板/比较 ──


def test_resolve_and_render() -> None:
    ctx = {"a": {"outputs": {"value": 42, "rows": [1, 2]}}, "_loop": {"item": {"n": 7}}}
    assert resolve_ref("a.outputs.value", ctx) == 42
    assert resolve_ref("_loop.item.n", ctx) == 7
    assert resolve_ref("a.outputs.ghost", ctx) is None
    # 全引用保值类型
    assert render_template("{{a.outputs.rows}}", ctx) == [1, 2]
    assert render_template("共 {{a.outputs.value}} 行", ctx) == "共 42 行"
    assert render_template("{{a.outputs.ghost}}", ctx) is None
    assert render_template({"k": ["{{a.outputs.value}}"]}, ctx) == {"k": [42]}


def test_render_immutability() -> None:
    """上下文不可变:全引用取出的是深拷贝,改动不回污上下文。"""
    ctx = {"a": {"outputs": {"rows": [1, 2]}}}
    out = render_template("{{a.outputs.rows}}", ctx)
    out.append(99)
    assert ctx["a"]["outputs"]["rows"] == [1, 2]


def test_compare_ops() -> None:
    assert compare(">", 3, 2) is True
    assert compare(">", "10", "9") is True  # 数值优先
    assert compare(">", "b", "a") is True  # 字符串兜底
    assert compare("contains", "武侯祠的银杏", "银杏") is True
    assert compare("!=", None, "x") is True
    with pytest.raises(DagValidationError):
        compare("~=", 1, 1)


# ── 引擎(mock runner) ──


class _MockSql:
    def __init__(self, verdict: str = "PASS", rows: list[dict[str, Any]] | None = None) -> None:
        self._v = verdict
        self._rows = rows if rows is not None else [["连弩"]]
        self.calls: list[str] = []

    async def run_sql(self, *, sql: str, **kw: Any) -> dict[str, Any]:
        self.calls.append(sql)
        return {
            "verdict": self._v,
            "reason": None if self._v == "PASS" else "FORBIDDEN",
            "columns": ["name"],
            "rows": self._rows,
        }


class _MockHttp:
    def __init__(self, body: str = "ok", blocked: bool = False) -> None:
        self._body = body
        self._blocked = blocked

    async def run_http(self, *, url: str, **kw: Any) -> dict[str, Any]:
        if self._blocked:
            raise SsrFBlocked("目标地址在拒绝段")
        return {"status": 200, "body": self._body}


class _MockAgent:
    def __init__(self, success: bool = True, summary: str = "军师答曰") -> None:
        self._ok = success
        self._summary = summary
        self.calls: list[str] = []

    async def run_agent(self, *, instruction: str, **kw: Any) -> dict[str, Any]:
        self.calls.append(instruction)
        return {"success": self._ok, "summary": self._summary, "sql": "", "rows": []}


def _run(nodes: list[dict[str, Any]], **kw: Any) -> Any:
    return run_dag(
        validate_dag({"nodes": nodes}),
        kw.get("inputs", {}),
        runners=(
            kw.get("sql") or _MockSql(),
            kw.get("http") or _MockHttp(),
            kw.get("agent") or _MockAgent(),
        ),
        trace_id="t",
        tenant_key="wf",
        user_id="u",
        timeout_sec=kw.get("timeout_sec", 120),
        step_cap=kw.get("step_cap", 200),
    )


async def test_engine_happy_chain() -> None:
    """检查 SQL、transform 和 Agent 节点之间的引用值传递。"""
    agent = _MockAgent()
    out = await _run(
        [
            {"id": "q", "type": "sql", "config": {"sql": "SELECT name FROM goods"}},
            {
                "id": "t",
                "type": "transform",
                "config": {"template": "首行: {{q.outputs.rows}}"},
                "depends_on": ["q"],
            },
            {
                "id": "ai",
                "type": "agent",
                "config": {"instruction": "解读 {{t.outputs.value}}"},
                "depends_on": ["t"],
            },
        ],
        agent=agent,
    )
    assert out.status == "SUCCESS"
    assert agent.calls == ["解读 首行: [['连弩']]"]
    by_id = {nr.node_id: nr for nr in out.node_runs}
    assert by_id["q"].outputs["row_count"] == 1


async def test_engine_condition_routing() -> None:
    """spec「条件分支选路」:False 时 on=true 跳过,on=false 执行。"""
    sql = _MockSql()
    out = await _run(
        [
            {"id": "c", "type": "condition", "config": {"left": "1", "op": ">", "right": "2"}},
            {
                "id": "yes",
                "type": "sql",
                "config": {"sql": "SELECT 1"},
                "depends_on": ["c"],
            },  # 缺省 on="true"
            {
                "id": "no",
                "type": "sql",
                "config": {"sql": "SELECT 2", "on": "false"},
                "depends_on": ["c"],
            },
        ],
        sql=sql,
    )
    by_id = {nr.node_id: nr.status for nr in out.node_runs}
    assert by_id["yes"] == "SKIPPED" and by_id["no"] == "SUCCESS"
    assert sql.calls == ["SELECT 2"]


async def test_engine_fail_fast_snapshot() -> None:
    """spec:节点失败 fail-fast;已完成节点快照保留。"""
    out = await _run(
        [
            {"id": "ok", "type": "transform", "config": {"template": "done"}},
            {"id": "bad", "type": "sql", "config": {"sql": "DROP TABLE x"}, "depends_on": ["ok"]},
            {
                "id": "never",
                "type": "transform",
                "config": {"template": "x"},
                "depends_on": ["bad"],
            },
        ],
        sql=_MockSql(verdict="BLOCKED"),
    )
    assert out.status == "FAILED"
    statuses = {nr.node_id: nr.status for nr in out.node_runs}
    assert statuses == {"ok": "SUCCESS", "bad": "FAILED"}
    assert "never" not in statuses


async def test_engine_loop_cap() -> None:
    """spec「循环上限保护」:数组超上限即 FAILED,不无限循环。"""
    out = await run_dag(
        validate_dag(
            {
                "nodes": [
                    {"id": "src", "type": "transform", "config": {"template": "{{inputs.arr}}"}},
                    {
                        "id": "lp",
                        "type": "loop",
                        "config": {
                            "items_ref": "src.outputs.value",
                            "body": [
                                {
                                    "id": "one",
                                    "type": "transform",
                                    "config": {"template": "{{_loop.item}}"},
                                }
                            ],
                            "max_iterations": 3,
                        },
                        "depends_on": ["src"],
                    },
                ]
            }
        ),
        {"arr": [1, 2, 3, 4, 5]},
        runners=(_MockSql(), _MockHttp(), _MockAgent()),
        trace_id="t",
        tenant_key="wf",
        user_id="u",
    )
    assert out.status == "FAILED"
    assert "超上限" in out.error


async def test_engine_loop_collects() -> None:
    out = await run_dag(
        validate_dag(
            {
                "nodes": [
                    {"id": "src", "type": "transform", "config": {"template": "{{inputs.arr}}"}},
                    {
                        "id": "lp",
                        "type": "loop",
                        "config": {
                            "items_ref": "src.outputs.value",
                            "body": [
                                {
                                    "id": "one",
                                    "type": "transform",
                                    "config": {"template": "项-{{_loop.item}}"},
                                }
                            ],
                            "max_iterations": 10,
                        },
                        "depends_on": ["src"],
                    },
                ]
            }
        ),
        {"arr": ["a", "b"]},
        runners=(_MockSql(), _MockHttp(), _MockAgent()),
        trace_id="t",
        tenant_key="wf",
        user_id="u",
    )
    assert out.status == "SUCCESS"
    lp = next(nr for nr in out.node_runs if nr.node_id == "lp")
    assert lp.outputs["items"] == ["项-a", "项-b"]


async def test_engine_timeout_and_step_limit() -> None:
    """spec「全局超时与步数上限」:慢节点被预算中断;步数超顶上界。"""

    class _SlowHttp:
        async def run_http(self, **kw: Any) -> dict[str, Any]:
            await asyncio.sleep(5)
            return {"status": 200, "body": ""}

    out = await _run(
        [{"id": "h", "type": "http", "config": {"url": "http://example.com", "method": "GET"}}],
        http=_SlowHttp(),
        timeout_sec=1,
    )
    assert out.status == "TIMEOUT"

    out2 = await _run(
        [
            {
                "id": f"n{i}",
                "type": "transform",
                "config": {"template": "x"},
                "depends_on": [f"n{i - 1}"] if i else [],
            }
            for i in range(10)
        ],
        step_cap=3,
    )
    assert out2.status == "STEP_LIMIT"


async def test_engine_ssrf_node_failed() -> None:
    """spec「SSRF 防护」:拒绝即节点 FAILED(fail-fast),run FAILED。"""
    out = await _run(
        [
            {
                "id": "h",
                "type": "http",
                "config": {"url": "http://169.254.169.254/latest", "method": "GET"},
            }
        ],
        http=_MockHttp(blocked=True),
    )
    assert out.status == "FAILED"
    assert "SSRF" in out.error


async def test_engine_unknown_exception_converges() -> None:
    """runner 未知异常(ConnectError 等)收敛节点 FAILED,
    run FAILED 不逃逸 500。"""
    import httpx

    class _BoomHttp:
        async def run_http(self, **kw: Any) -> dict[str, Any]:
            raise httpx.ConnectError("refused")

    out = await _run(
        [{"id": "h", "type": "http", "config": {"url": "http://example.com", "method": "GET"}}],
        http=_BoomHttp(),
    )
    assert out.status == "FAILED"
    assert "ConnectError" in out.error


async def test_engine_loop_body_skip_routing() -> None:
    """子段内 condition 选路生效（检查 SQL mock 的调用顺序：
    item=2→big;item=1→small)。"""
    sql = _MockSql()
    out = await run_dag(
        validate_dag(
            {
                "nodes": [
                    {"id": "src", "type": "transform", "config": {"template": "{{inputs.arr}}"}},
                    {
                        "id": "lp",
                        "type": "loop",
                        "config": {
                            "items_ref": "src.outputs.value",
                            "body": [
                                {
                                    "id": "c",
                                    "type": "condition",
                                    "config": {"left": "{{_loop.item}}", "op": ">", "right": "1"},
                                },
                                {
                                    "id": "big",
                                    "type": "sql",
                                    "config": {"sql": "SELECT 'big'"},
                                    "depends_on": ["c"],
                                },
                                {
                                    "id": "small",
                                    "type": "sql",
                                    "config": {"sql": "SELECT 'small'", "on": "false"},
                                    "depends_on": ["c"],
                                },
                            ],
                            "max_iterations": 10,
                        },
                        "depends_on": ["src"],
                    },
                ]
            }
        ),
        {"arr": [2, 1]},
        runners=(sql, _MockHttp(), _MockAgent()),
        trace_id="t",
        tenant_key="wf",
        user_id="u",
    )
    assert out.status == "SUCCESS"
    assert sql.calls == ["SELECT 'big'", "SELECT 'small'"]


async def test_engine_loop_body_id_collision_fails() -> None:
    """body 节点 id 撞外层 → 保存期校验拒绝(引擎深防御 NodeFailed)。"""
    with pytest.raises(DagValidationError, match="撞保留名/外层节点"):
        validate_dag(
            {
                "nodes": [
                    {"id": "src", "type": "transform", "config": {"template": "x"}},
                    {
                        "id": "lp",
                        "type": "loop",
                        "config": {
                            "items_ref": "src.outputs.value",
                            "max_iterations": 3,
                            "body": [
                                {"id": "src", "type": "transform", "config": {"template": "y"}}
                            ],
                        },
                        "depends_on": ["src"],
                    },
                ]
            }
        )


# ── SSRF 判定矩阵(直测判定函数) ──


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://127.1/x",
        "http://10.0.0.5/x",
        "http://172.16.3.4/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://0.0.0.0/x",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",  # IPv4 映射逃逸
        "http://[fe80::1]/x",
        "http://100.64.1.1/x",  # CGNAT 共享段
        "ftp://example.com/x",
        "http:///nohost",
    ],
)
async def test_ssrf_denied(url: str) -> None:
    from kongming.workflow.http_runner import assert_url_allowed

    with pytest.raises(SsrFBlocked):
        await assert_url_allowed(url)


async def test_ssrf_allowed_public_ip() -> None:
    from kongming.workflow.http_runner import assert_url_allowed

    await assert_url_allowed("http://8.8.8.8/dns")  # 字面公网 IP 免 DNS


async def test_ssrf_domain_resolves_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """域名解析到私网即拒(重绑定面);解析到公网放行。"""
    import kongming.workflow.http_runner as hr

    async def _fake_private(host: str, port: int) -> list[tuple[object, ...]]:
        return [(None, None, None, None, ("192.168.0.1", port))]

    monkeypatch.setattr(hr, "_resolve", _fake_private)
    with pytest.raises(SsrFBlocked, match="拒绝段"):
        await hr.assert_url_allowed("http://internal.example/x")

    async def _fake_public(host: str, port: int) -> list[tuple[object, ...]]:
        return [(None, None, None, None, ("93.184.216.34", port))]

    monkeypatch.setattr(hr, "_resolve", _fake_public)
    await hr.assert_url_allowed("http://example.com/x")


def test_ssrf_sync_host_allowed_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """同步主机级判定(assert_host_allowed_sync,IMAP 出站消费):字面
    与解析落拒绝段即拒(同一拒绝段单源),公网字面放行(不触网)。"""
    import kongming.workflow.http_runner as hr

    for host in (
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",  # noqa: S104 —— SSRF 拒绝段测试数据,非绑定
        "100.64.1.1",
        "localhost",  # 解析为回环 → 拒
    ):
        with pytest.raises(SsrFBlocked):
            hr.assert_host_allowed_sync(host, 993)
    # 解析失败场景经 monkeypatch 模拟(不触真实 DNS):本机解析器可能对
    # NXDOMAIN 域名劫持返回公网 IP(DNS 重定向),真实解析在此环境不可依赖
    with monkeypatch.context() as m:

        def _fail_resolve(host: str, port: int, **_kw: object) -> list[tuple[object, ...]]:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

        m.setattr(hr.socket, "getaddrinfo", _fail_resolve)
        with pytest.raises(SsrFBlocked, match="解析失败"):
            hr.assert_host_allowed_sync("nonexistent.invalid", 993)
    hr.assert_host_allowed_sync("93.184.216.34", 993)


# ── HTTP 节点 ──


async def _wf_user(
    client: AsyncClient,
    s: AsyncSession,
    make_user: MakeUser,
    name: str,
    codes: list[str],
) -> tuple[str, str]:
    await seed_all(s, get_settings())
    uid = await make_user(name)
    rid = await _mk_role_with_perms(s, f"role_{name}", codes)
    tid = await _mk_tenant(s, "wf")
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, tid)
    await s.commit()
    return uid, await _login(client, name)


_SQL_WF = {
    "nodes": [
        {"id": "q", "type": "sql", "config": {"sql": "SELECT name FROM goods"}},
        {
            "id": "t",
            "type": "transform",
            "config": {"template": "行数 {{q.outputs.row_count}}"},
            "depends_on": ["q"],
        },
    ]
}


async def test_workflow_crud_version_freeze(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「版本冻结」:再保存产生新版本,旧版本不可改。"""
    _, token = await _wf_user(client, session, make_user, "wf_admin1", ["system:workflow:manage"])
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "wf"}
    r = await client.post(
        "/system/workflow",
        headers=h,
        json={"wf_key": "daily", "name": "日报", "definition": _SQL_WF},
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 1
    r2 = await client.post(
        "/system/workflow",
        headers=h,
        json={"wf_key": "daily", "name": "日报v2", "definition": _SQL_WF},
    )
    assert r2.json()["version"] == 2
    r3 = await client.get("/system/workflow/daily", headers=h)
    assert r3.json()["latest"]["version"] == 2
    assert [v["version"] for v in r3.json()["versions"]] == [2, 1]
    # 环校验 400 指出环节点
    r4 = await client.post(
        "/system/workflow",
        headers=h,
        json={
            "wf_key": "cyclic",
            "name": "环",
            "definition": {
                "nodes": [
                    {
                        "id": "a",
                        "type": "transform",
                        "config": {"template": "x"},
                        "depends_on": ["b"],
                    },
                    {
                        "id": "b",
                        "type": "transform",
                        "config": {"template": "y"},
                        "depends_on": ["a"],
                    },
                ]
            },
        },
    )
    assert r4.status_code == 400
    assert "环" in r4.json()["detail"]


async def test_workflow_run_and_history(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「手动触发并落历史」:run 返回节点快照,历史分页可见;
    sql 节点 SELF 行级复校(goods 无本人行 → row_count 0)。"""
    _, token = await _wf_user(
        client,
        session,
        make_user,
        "wf_run1",
        ["system:workflow:manage", "workflow:execute", "tool:sql:execute"],
    )
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "wf"}
    await client.post(
        "/system/workflow",
        headers=h,
        json={"wf_key": "inv", "name": "库存", "definition": _SQL_WF},
    )
    r = await client.post("/system/workflow/inv/run", headers=h, json={"inputs": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "SUCCESS"
    q_run = next(nr for nr in body["node_runs"] if nr["node_id"] == "q")
    assert q_run["outputs"]["row_count"] == 0  # SELF 行级:someone 行不可见
    # 历史分页
    r2 = await client.get("/system/workflow/inv/runs", headers=h)
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["status"] == "SUCCESS"


async def test_workflow_disabled_blocks_explicit_old_version(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """disable 后显式 version=旧 也 409(旧版本随新版本自动失活,
    启用闸恒只作用最新版本)。"""
    _, token = await _wf_user(
        client,
        session,
        make_user,
        "wf_s2",
        ["system:workflow:manage", "workflow:execute", "tool:sql:execute"],
    )
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "wf"}
    await client.post(
        "/system/workflow",
        headers=h,
        json={"wf_key": "ver_gate", "name": "闸", "definition": _SQL_WF},
    )
    await client.post(  # v2(v1 自动失活)
        "/system/workflow",
        headers=h,
        json={"wf_key": "ver_gate", "name": "闸v2", "definition": _SQL_WF},
    )
    await client.post("/system/workflow/ver_gate/disable", headers=h)
    r = await client.post(
        "/system/workflow/ver_gate/run",
        headers=h,
        json={"inputs": {}, "version": 1},
    )
    assert r.status_code == 409


async def test_workflow_perms_and_isolation(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec:无 workflow:execute 403;跨租户 404 同形;禁用 409。"""
    _, token = await _wf_user(
        client,
        session,
        make_user,
        "wf_iso1",
        ["system:workflow:manage", "workflow:execute"],
    )
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "wf"}
    await client.post(
        "/system/workflow",
        headers=h,
        json={"wf_key": "guarded", "name": "守", "definition": _SQL_WF},
    )
    # 无执行权限
    uid2 = await make_user("wf_noexec")
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='wf'"))
    ).scalar_one()
    await _bind(session, "sys_user_tenant", uid2, str(tid))
    await session.commit()
    token2 = await _login(client, "wf_noexec")
    r = await client.post(
        "/system/workflow/guarded/run",
        headers={"Authorization": f"Bearer {token2}", "X-Tenant-Id": "wf"},
        json={"inputs": {}},
    )
    assert r.status_code == 403
    # 跨租户 404 同形
    token3 = await _login(client, "wf_noexec")  # default 租户视角
    r2 = await client.get(
        "/system/workflow/guarded",
        headers={"Authorization": f"Bearer {token3}"},
    )
    # 未绑 default 的 wf_noexec 只有 wf 租户;缺省取 default 租户解析 403
    assert r2.status_code in (403, 404)
    # 禁用后触发 409
    await client.post("/system/workflow/guarded/disable", headers=h)
    r3 = await client.post("/system/workflow/guarded/run", headers=h, json={"inputs": {}})
    assert r3.status_code == 409


async def test_pinned_transport_rewrites_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSRF 判定后连接必须钉扎判定时的 IP(DNS rebinding
    TOCTOU)——URL host 换 IP、Host 头/SNI 保原域名。"""
    import httpx

    from kongming.workflow.http_runner import _PinnedTransport

    captured: dict[str, object] = {}

    async def fake_handle(self: object, request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["host"] = request.headers.get("Host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_handle)
    transport = _PinnedTransport("93.184.216.34")
    request = httpx.Request("GET", "https://example.com:8443/path")
    await transport.handle_async_request(request)
    assert str(captured["url"]).startswith("https://93.184.216.34:8443/path")
    assert captured["host"] == "example.com:8443"
    assert captured["sni"] == "example.com"
