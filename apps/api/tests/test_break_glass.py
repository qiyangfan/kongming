"""break-glass CLI 回归:凭证/时间窗/二次确认/影响行数/审计;无 admin 端点。"""

import importlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import db_conn


def _module() -> Any:
    return importlib.import_module("scripts.break_glass")


def _env(monkeypatch: pytest.MonkeyPatch, **over: str) -> None:
    conn = db_conn()
    url = f"postgresql+asyncpg://{conn['username']}:{conn['password']}@{conn['host']}:{conn['port']}/{conn['database']}"
    base = {
        "KM_BREAK_GLASS_DATABASE_URL": url,
        "KM_BREAK_GLASS_TOKEN": "bg-secret",
        "KM_BREAK_GLASS_ACTOR": "ops@example",
        "KM_BREAK_GLASS_EXPIRES_AT": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    base.update(over)
    for k, v in base.items():
        monkeypatch.setenv(k, v)


async def _mk_task(session: AsyncSession, status: str = "RUNNING") -> str:
    task_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_agent_task (id, tenant_id, user_id, goal, status)"
            " VALUES (CAST(:i AS uuid), gen_random_uuid(), gen_random_uuid(), 'x', :s)"
        ),
        {"i": task_id, "s": status},
    )
    await session.commit()
    return task_id


async def test_no_credential_refuses_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, KM_BREAK_GLASS_DATABASE_URL="", KM_BREAK_GLASS_TOKEN="")
    m = _module()
    code = await m._main_async(
        ["--scenario", "mark-task-interrupted", "--id", str(uuid.uuid4()), "--reason", "修复原因"]
    )
    assert code == 2


async def test_expired_window_and_bad_token_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    _env(monkeypatch, KM_BREAK_GLASS_EXPIRES_AT=past)
    m = _module()
    argv = [
        "--scenario",
        "mark-task-interrupted",
        "--id",
        str(uuid.uuid4()),
        "--reason",
        "修复原因",
    ]
    assert await m._main_async(argv) == 2
    _env(monkeypatch)
    assert await m._main_async([*argv, "--token", "wrong"]) == 2


async def test_apply_requires_confirm_and_writes_audit(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    _env(monkeypatch)
    task_id = await _mk_task(session)
    m = _module()
    base = [
        "--scenario",
        "mark-task-interrupted",
        "--id",
        task_id,
        "--reason",
        "看门狗失效后人工收口",
        "--token",
        "bg-secret",
        "--apply",
    ]
    assert await m._main_async(base) == 2  # 缺确认短语
    assert await m._main_async([*base, "--confirm", f"APPLY mark-task-interrupted {task_id}"]) == 0
    status = (
        await session.execute(
            text("SELECT status FROM public.sys_agent_task WHERE id = CAST(:i AS uuid)"),
            {"i": task_id},
        )
    ).scalar_one()
    assert status == "INTERRUPTED"
    row = (
        await session.execute(
            text(
                "SELECT action, detail FROM public.sys_oper_log"
                " WHERE action = 'break_glass.mark-task-interrupted'"
            )
        )
    ).one()
    assert cast(dict[str, object], row[1])["actor"] == "ops@example"
    assert cast(dict[str, object], row[1])["rows"] == 1


async def test_dry_run_does_not_write(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    _env(monkeypatch)
    task_id = await _mk_task(session)
    m = _module()
    assert (
        await m._main_async(
            [
                "--scenario",
                "mark-task-interrupted",
                "--id",
                task_id,
                "--reason",
                "仅演练不写入数据库",
                "--token",
                "bg-secret",
            ]
        )
        == 0
    )
    status = (
        await session.execute(
            text("SELECT status FROM public.sys_agent_task WHERE id = CAST(:i AS uuid)"),
            {"i": task_id},
        )
    ).scalar_one()
    assert status == "RUNNING"
    logs = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action LIKE 'break_glass.%'")
        )
    ).scalar_one()
    assert int(logs) == 0


async def test_no_admin_break_glass_endpoint() -> None:
    from kongming.main import app

    paths = set(app.openapi()["paths"])
    assert not any("break-glass" in p or "break_glass" in p for p in paths)
