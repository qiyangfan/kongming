"""覆盖沙箱工具的注册、预检、分发、审计和 Podman 隔离。"""
# pyright: reportPrivateUsage=false

import json
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.db import get_engine
from kongming.sandbox.models import SandboxOutcome
from kongming.sandbox.runner import _parse_outcome
from kongming.toolcall.ports import SandboxExecutor
from kongming.toolcall.registry import filter_specs
from tests.conftest import MakeUser
from tests.test_chat_query import _bind
from tests.test_toolcall import (
    INTERNAL,
    _mk_role_with_perms,
    _mk_tenant,
    _req,
    _tool_audits,
)

SCHEMA = "tenant_sbx"
SANDBOX_IMAGE = "kongming-sandbox:dev"


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """沙箱测试租户业务 schema(data_sql 真执行活数据,与 test_toolcall 同形态)。"""
    async with get_engine().begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))  # noqa: S608  # 模块常量
        await conn.execute(
            text(
                f'CREATE TABLE IF NOT EXISTS "{SCHEMA}".goods ('  # noqa: S608  # 模块常量
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
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))  # noqa: S608  # 模块常量


async def _sbx_user(s: AsyncSession, make_user: MakeUser, username: str, codes: list[str]) -> str:
    """造一个绑 sbx 租户+指定权限角色的用户(租户业务 schema=tenant_sbx)。"""
    uid = await make_user(username)
    rid = await _mk_role_with_perms(s, f"role_{username}", codes)
    tid = await _mk_tenant(s, "sbx")
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, tid)
    await s.commit()
    return uid


# ── 注册表与参数预检(纯函数层) ──


def test_perm_filter_gates_schema() -> None:
    """spec「未授权不注入」:权限点集不含 tool:code:execute 时 schema 不注入。"""
    names_without = [t["name"] for t in filter_specs({"tool:sql:execute"})]
    assert "run_python_code" not in names_without
    names_with = [t["name"] for t in filter_specs({"tool:code:execute"})]
    assert "run_python_code" in names_with
    # 既有四工具(spec「与既有工具」)
    all_names = [
        t["name"]
        for t in filter_specs(
            {
                "tool:sql:validate",
                "tool:sql:execute",
                "tool:rag:retrieve",
                "tool:announcement:create",
            }
        )
    ]
    assert all_names == [
        "validate_sql",
        "execute_sql",
        "rag_retrieve",
        "create_system_announcement",
    ]


def test_validate_args_code_and_data_sql() -> None:
    """L4:code 上限 8000;data_sql 根节点只读预检(映射表)。"""
    from kongming.toolcall.registry import get_tool
    from kongming.toolcall.service import validate_args

    tool = get_tool("run_python_code")
    assert tool is not None
    assert validate_args(tool, {"code": "print(1)"}) is None
    assert validate_args(tool, {"code": "print(1)", "data_sql": "SELECT 1"}) is None
    assert validate_args(tool, {"code": "x" * 8001}) is not None  # 超上限
    err = validate_args(tool, {"code": "print(1)", "data_sql": "DELETE FROM goods"})
    assert err is not None and "只读" in err
    # 语法畸形放行下游(防火墙接管),与 sql 参数同语义
    assert validate_args(tool, {"code": "print(1)", "data_sql": "SELCT ("}) is None


# ── 协议解析纯函数(宿主侧分类) ──


def test_parse_outcome_success() -> None:
    body = {
        "status": "SUCCESS",
        "stdout": "hi",
        "error": None,
        "images": ["YmFzZTY0"],
        "data_rows": 3,
        "data_cols": 2,
    }
    out = _parse_outcome(
        stdout_text=f"noise\n__KM_RESULT__{json.dumps(body)}",
        stderr_tail="",
        exit_code=0,
        duration_ms=12,
    )
    assert out.status == "SUCCESS" and out.stdout == "hi"
    assert out.images == ["YmFzZTY0"] and out.data_rows == 3 and out.data_cols == 2


def test_parse_outcome_fake_protocol_line_loses() -> None:
    """验证宿主只读取 stdout 最后一行，用户伪造的协议行不会覆盖真实结果。"""
    fake = "__KM_RESULT__" + json.dumps({"status": "SUCCESS", "stdout": "pwned"})
    real = "__KM_RESULT__" + json.dumps({"status": "ERROR", "error": "boom"})
    out = _parse_outcome(stdout_text=f"{fake}\n{real}", stderr_tail="", exit_code=1, duration_ms=5)
    assert out.status == "ERROR" and out.error == "boom"


def test_parse_outcome_oom_137() -> None:
    out = _parse_outcome(stdout_text="", stderr_tail="", exit_code=137, duration_ms=9)
    assert out.status == "RESOURCE_LIMIT" and out.exit_code == 137


def test_parse_outcome_missing_protocol() -> None:
    out = _parse_outcome(
        stdout_text="partial output",
        stderr_tail="exec: python: not found",
        exit_code=127,
        duration_ms=3,
    )
    assert out.status == "ERROR" and "not found" in (out.error or "")


def test_parse_outcome_malformed_protocol() -> None:
    out = _parse_outcome(
        stdout_text="__KM_RESULT__{not-json", stderr_tail="", exit_code=0, duration_ms=3
    )
    assert out.status == "ERROR" and out.error == "沙箱协议行畸形"


def test_parse_outcome_forged_fields_converge() -> None:
    """伪造协议行的 int 字段畸形不逃逸(端口零抛出契约)。"""
    body = {"status": "SUCCESS", "data_rows": "abc", "data_cols": None}
    out = _parse_outcome(
        stdout_text=f"__KM_RESULT__{json.dumps(body)}",
        stderr_tail="",
        exit_code=0,
        duration_ms=3,
    )
    assert out.status == "ERROR" and out.error == "沙箱协议行畸形"


def test_parse_outcome_images_host_recheck() -> None:
    """宿主复核 images——至多 1 张,单张 ≤500KB,非字符串剔除。"""
    big = "y" * (500 * 1024 + 1)
    body = {"status": "SUCCESS", "images": ["aW1n", big, "c2Vjb25k", 123]}
    out = _parse_outcome(
        stdout_text=f"__KM_RESULT__{json.dumps(body)}",
        stderr_tail="",
        exit_code=0,
        duration_ms=3,
    )
    assert out.status == "SUCCESS"
    assert out.images == ["aW1n"]  # 超容与超额均剔除(非 str 亦剔除)


# ── HTTP 分发与沙箱防护 ──


class _RecordingSandbox:
    """SandboxExecutor 结构型 mock:记录入参,返回固定投影。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(
        self,
        *,
        code: str,
        data: dict[str, object] | None,
        trace_id: str,
        user_id: str,
        tenant_id: str,
    ) -> dict[str, object]:
        self.calls.append({"code": code, "data": data, "trace_id": trace_id})
        rows = cast("list[object]", (data or {}).get("rows") or [])
        cols = cast("list[object]", (data or {}).get("columns") or [])
        proj: dict[str, object] = {
            "status": "SUCCESS",
            "stdout": "ok",
            "error": None,
            "images_count": 0,
            "duration_ms": 7,
        }
        if data is not None:
            proj["injected"] = f"已注入 df({len(rows)}×{len(cols)})"
        return proj


def _override_service(sandbox: object | None) -> None:  # noqa: ANN401
    """dependency_overrides 接收工厂;直接传类会被当作模型构造并返回 422。

    会话由请求级 get_session 子依赖提供并关闭,避免绕过连接池生命周期。
    除沙箱外,其余依赖保持真实。
    """
    from fastapi import Depends

    from kongming.auth.repository import SqlPermRepo
    from kongming.db import get_session
    from kongming.main import app
    from kongming.redis_client import get_redis
    from kongming.toolcall.ratelimit import RedisRateLimiter
    from kongming.toolcall.repository import SqlToolCallRepo, ToolAuditWriter
    from kongming.toolcall.router import (
        _ChatReplyStore,
        _get_service,
        _InternalSqlExecutor,
        _RagServiceAdapter,
    )
    from kongming.toolcall.service import ToolCallService

    def _factory(
        s: AsyncSession = Depends(get_session),  # noqa: B008  # FastAPI 依赖注入惯例
    ) -> ToolCallService:
        return ToolCallService(
            repo=SqlToolCallRepo(s),
            writer=ToolAuditWriter(),
            perms=SqlPermRepo(s),
            limiter=RedisRateLimiter(get_redis()),
            sql_executor=_InternalSqlExecutor(s),
            rag_retriever=_RagServiceAdapter(s),
            reply_store=_ChatReplyStore(s),
            intent=None,
            sandbox=cast("SandboxExecutor | None", sandbox),
            rate_limit_per_minute=600,
            confirm_ttl_minutes=15,
        )

    app.dependency_overrides[_get_service] = _factory


def _clear_override() -> None:
    from kongming.main import app
    from kongming.toolcall.router import _get_service

    app.dependency_overrides.pop(_get_service, None)


async def test_unauthorized_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「未授权不注入」:无 tool:code:execute 权限直调按越权拒绝。"""
    await seed_all(session, get_settings())
    uid = await _sbx_user(session, make_user, "sbx_noperm", ["tool:sql:execute"])
    r = await client.post(
        "/internal/tool/execute",
        headers=INTERNAL,
        json=_req("run_python_code", {"code": "print(1)"}, user_id=uid, tenant_key="sbx"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected"
    assert "TOOL_PERMISSION_DENIED" in (body["reason"] or "")


async def test_disabled_converges_error(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「未启用友好降级」:sandbox=None → error+「沙箱未启用」+审计落行。"""
    await seed_all(session, get_settings())
    uid = await _sbx_user(session, make_user, "sbx_off", ["tool:code:execute"])
    trace = f"tr-{uuid.uuid4().hex[:12]}"
    _override_service(None)
    try:
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req(
                "run_python_code",
                {"code": "print(1)"},
                user_id=uid,
                trace_id=trace,
                tenant_key="sbx",
            ),
        )
    finally:
        _clear_override()
    body = r.json()
    assert body["status"] == "error"
    assert "沙箱未启用" in (body["reason"] or "")
    audits = await _tool_audits(session, trace)
    assert audits and audits[0]["status"] == "ERROR"


async def test_happy_path_injects_df(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """验证取数工具通过受控 SQL 获取数据，再将 DataFrame 注入沙箱。"""
    await seed_all(session, get_settings())
    uid = await _sbx_user(session, make_user, "sbx_ok", ["tool:code:execute"])
    sandbox = _RecordingSandbox()
    trace = f"tr-{uuid.uuid4().hex[:12]}"
    _override_service(sandbox)
    try:
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req(
                "run_python_code",
                {"code": "print(df.shape)", "data_sql": "SELECT name FROM goods"},
                user_id=uid,
                trace_id=trace,
                tenant_key="sbx",
            ),
        )
    finally:
        _clear_override()
    body = r.json()
    assert body["status"] == "executed"
    result = cast("dict[str, Any]", body["result"] or {})
    # SELF 行级:goods 仅 create_by='someone' 行,本人不可见 → 0×1
    assert result.get("injected") == "已注入 df(0×1)"
    assert len(sandbox.calls) == 1
    data = sandbox.calls[0]["data"]
    assert isinstance(data, dict) and cast("dict[str, object]", data).get("columns") == ["name"]
    assert cast("dict[str, object]", data).get("rows") == []  # 行级权限过滤后不返回结果。


async def test_data_sql_blocked_no_sandbox(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec:data_sql 未过检(保留表)直接返回,沙箱不启动。"""
    await seed_all(session, get_settings())
    uid = await _sbx_user(session, make_user, "sbx_blk", ["tool:code:execute"])
    sandbox = _RecordingSandbox()
    _override_service(sandbox)
    try:
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req(
                "run_python_code",
                {"code": "print(1)", "data_sql": "SELECT id FROM public.sys_user"},
                user_id=uid,
                tenant_key="sbx",
            ),
        )
    finally:
        _clear_override()
    body = r.json()
    assert body["status"] == "executed"  # 工具层执行成功,业务结果为取数拒绝
    result = cast("dict[str, Any]", body["result"] or {})
    assert result.get("status") == "ERROR"
    assert result.get("verdict") == "BLOCKED"
    assert sandbox.calls == []  # 沙箱未启动


async def test_inject_over_1mb_rejected(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「注入超限拒绝」:data_sql 结果 JSON 化超 1MB → ERROR,沙箱不启动。"""
    await seed_all(session, get_settings())
    uid = await _sbx_user(session, make_user, "sbx_big", ["tool:code:execute"])
    # repeat 撑爆 1MB(~1100 字符 × 1000 行 ≈ 1.1MB);无表 SELECT 的 SELF
    # 降级为不过滤(datascope 立约),repeat/generate_series 不在危险函数清单
    sandbox = _RecordingSandbox()
    _override_service(sandbox)
    try:
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req(
                "run_python_code",
                {
                    "code": "print(1)",
                    "data_sql": "SELECT repeat('x', 1100) AS pad FROM generate_series(1, 1000)",
                },
                user_id=uid,
                tenant_key="sbx",
            ),
        )
    finally:
        _clear_override()
        await session.commit()
    body = r.json()
    result = cast("dict[str, Any]", body["result"] or {})
    assert result.get("status") == "ERROR"
    assert "上限" in str(result.get("error"))
    assert sandbox.calls == []


async def test_inject_gate_counts_bytes_cjk(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """注入内容按 UTF-8 字节数限制；多字节字符超过上限时应被拒绝。"""
    await seed_all(session, get_settings())
    uid = await _sbx_user(session, make_user, "sbx_cjk", ["tool:code:execute"])
    sandbox = _RecordingSandbox()
    _override_service(sandbox)
    try:
        r = await client.post(
            "/internal/tool/execute",
            headers=INTERNAL,
            json=_req(
                "run_python_code",
                {
                    "code": "print(1)",
                    "data_sql": "SELECT repeat('孔', 600) AS pad FROM generate_series(1, 1000)",
                },
                user_id=uid,
                tenant_key="sbx",
            ),
        )
    finally:
        _clear_override()
    result = cast("dict[str, Any]", (r.json())["result"] or {})
    assert result.get("status") == "ERROR"
    assert "上限" in str(result.get("error"))
    assert sandbox.calls == []


# ── 审计落库(service 级真 runner-mock + 真 writer) ──


async def test_audit_record_fields(session: AsyncSession) -> None:
    """spec「执行审计」:四终态字段全落 sys_sandbox_run(独立短事务)。"""
    from kongming.sandbox.repository import SqlSandboxRunRepo
    from kongming.sandbox.service import SandboxService

    class _MockRunner:
        def __init__(self, outcome: SandboxOutcome) -> None:
            self._o = outcome

        async def run(self, *, code: str, data: dict[str, object] | None) -> SandboxOutcome:
            return self._o

    await seed_all(session, get_settings())
    tid = (await session.execute(text("SELECT id FROM public.sys_tenant LIMIT 1"))).scalar_one()
    uid = (await session.execute(text("SELECT id FROM public.sys_user LIMIT 1"))).scalar_one()
    cases = [
        SandboxOutcome(
            status="SUCCESS", stdout="done", data_rows=5, data_cols=2, duration_ms=33, exit_code=0
        ),
        SandboxOutcome(status="ERROR", error="traceback…", duration_ms=12, exit_code=1),
        SandboxOutcome(status="TIMEOUT", error="执行超 30s 上限已终止", duration_ms=30001),
        SandboxOutcome(
            status="RESOURCE_LIMIT",
            error="资源超限已终止(内存配额)",
            duration_ms=800,
            exit_code=137,
        ),
    ]
    for oc in cases:
        svc = SandboxService(runner=_MockRunner(oc), writer=SqlSandboxRunRepo())
        proj = await svc.execute(
            code="print(1)",
            data={"columns": ["a"], "rows": [[1]]},
            trace_id=f"tr-{uuid.uuid4().hex[:12]}",
            user_id=str(uid),
            tenant_id=str(tid),
        )
        assert proj["status"] == oc.status
        assert proj["injected"] == f"已注入 df({oc.data_rows}×{oc.data_cols})"
    rows = (
        (
            await session.execute(
                text(
                    "SELECT status, data_rows, data_cols, stdout_tail, error_tail,"
                    " duration_ms, exit_code, code_text FROM public.sys_sandbox_run"
                    " ORDER BY created_at, id"
                )
            )
        )
        .mappings()
        .all()
    )
    assert [r["status"] for r in rows[-4:]] == [
        "SUCCESS",
        "ERROR",
        "TIMEOUT",
        "RESOURCE_LIMIT",
    ]
    first = rows[-4]
    assert first["data_rows"] == 5 and first["data_cols"] == 2
    assert first["stdout_tail"] == "done" and first["code_text"] == "print(1)"
    assert rows[-1]["exit_code"] == 137


# ── podman 集成(守卫:无二进制/无镜像自动 skip,CI 零炸) ──

_PODMAN = shutil.which("podman")


def _image_ready() -> bool:
    if _PODMAN is None:
        return False
    r = subprocess.run(  # noqa: S603  # 二进制经 which 解析,参数常量
        [_PODMAN, "image", "inspect", SANDBOX_IMAGE],
        capture_output=True,
        check=False,
    )
    return r.returncode == 0


requires_podman = pytest.mark.skipif(
    not _image_ready(),
    reason=f"需 podman 与镜像 {SANDBOX_IMAGE}(podman build -f infra/sandbox.Dockerfile "
    f"-t {SANDBOX_IMAGE} infra/)",
)


@requires_podman
async def test_podman_happy_df_and_plot() -> None:
    """真容器:df 注入求和 + stdout + plt 收图。"""
    from kongming.sandbox.runner import PodmanSandboxRunner

    runner = PodmanSandboxRunner(image=SANDBOX_IMAGE, timeout_sec=30)
    out = await runner.run(
        code="print('sum=', int(df['v'].sum()))\n"
        "import matplotlib.pyplot as plt\nplt.figure(); plt.plot([1,2]); plt.title('t')",
        data={"columns": ["v"], "rows": [[1], [2], [3]]},
    )
    assert out.status == "SUCCESS"
    assert "sum= 6" in out.stdout
    assert out.data_rows == 3 and out.data_cols == 1
    assert len(out.images) == 1  # 末图收集


@requires_podman
async def test_podman_no_network() -> None:
    """spec「网络隔离」:socket 外联失败(--network none)。"""
    from kongming.sandbox.runner import PodmanSandboxRunner

    runner = PodmanSandboxRunner(image=SANDBOX_IMAGE, timeout_sec=30)
    out = await runner.run(
        code="import socket\nsocket.create_connection(('8.8.8.8', 53), timeout=3)",
        data=None,
    )
    assert out.status == "ERROR"
    assert out.error is not None  # OSError 原文(traceback 尾)


@requires_podman
async def test_podman_memory_limit() -> None:
    """spec「内存超限终止」:大分配触 cgroup OOM → RESOURCE_LIMIT。"""
    from kongming.sandbox.runner import PodmanSandboxRunner

    runner = PodmanSandboxRunner(image=SANDBOX_IMAGE, timeout_sec=30, mem_mb=128)
    out = await runner.run(code="x = bytearray(512 * 1024 * 1024)\nprint(len(x))", data=None)
    assert out.status == "RESOURCE_LIMIT"


@requires_podman
async def test_podman_timeout() -> None:
    """spec「执行超时终止」:sleep 超 2s 上限 → TIMEOUT。"""
    from kongming.sandbox.runner import PodmanSandboxRunner

    runner = PodmanSandboxRunner(image=SANDBOX_IMAGE, timeout_sec=2)
    out = await runner.run(code="import time\ntime.sleep(30)", data=None)
    assert out.status == "TIMEOUT"


@requires_podman
async def test_podman_output_flood_killed() -> None:
    """容器内进程绕过 stdout 限制输出超过 2 MB 时，宿主总量限制会终止容器。"""
    from kongming.sandbox.runner import PodmanSandboxRunner

    runner = PodmanSandboxRunner(image=SANDBOX_IMAGE, timeout_sec=30)
    out = await runner.run(
        code=(
            "import os\n"
            "chunk = b'x' * 65536\n"
            "for _ in range(200): os.write(1, chunk)"  # ~13MB 直写 fd 1
        ),
        data=None,
    )
    assert out.status == "RESOURCE_LIMIT"
    assert "上限" in (out.error or "")


@requires_podman
async def test_podman_no_residue() -> None:
    """spec「即起即销无残留」:执行后无 km-sbx- 容器残留(含失败态)。"""
    from kongming.sandbox.runner import PodmanSandboxRunner

    runner = PodmanSandboxRunner(image=SANDBOX_IMAGE, timeout_sec=15)
    await runner.run(code="print(1)", data=None)
    await runner.run(code="raise ValueError('x')", data=None)
    r = subprocess.run(  # noqa: S603, ASYNC221  # which 解析+参数常量;测试收尾一次性同步调用
        [_PODMAN or "podman", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert "km-sbx-" not in r.stdout
