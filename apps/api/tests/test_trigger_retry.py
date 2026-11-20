"""trigger 重试策略测试:重试判定纯函数/调度扫描派发/重试链集成
(先败后成、耗尽)/停用删除取消/notify 终态化/迁移契约。"""
# pyright: reportPrivateUsage=false

import asyncio
import datetime as dt
import json
import uuid as uuid_mod
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.trigger.repository import SqlTriggerRepo
from kongming.trigger.retry import schedule_retry
from tests.conftest import migrated_tmp_db
from tests.test_trigger import _scheduler_with, _StubService

_TZ = dt.timezone(dt.timedelta(hours=8))
_T0 = dt.datetime(2026, 10, 11, 9, 0, 0, tzinfo=_TZ)


# ── 重试判定纯函数(边界逐类:成功/SKIPPED/0 次/耗尽/安排) ──


def test_retry_pure_success_terminal() -> None:
    """链内任一次 SUCCESS 即链终态成功,不再安排重试。"""
    assert (
        schedule_retry(status="SUCCESS", attempt=1, max_retries=3, now=_T0, backoff_sec=60) is None
    )


def test_retry_pure_skipped_never_retries() -> None:
    """SKIPPED 是并发保护语义不是执行失败,永不重试(任意尝试序号)。"""
    for att in (1, 2, 5):
        assert (
            schedule_retry(status="SKIPPED", attempt=att, max_retries=3, now=_T0, backoff_sec=60)
            is None
        )


def test_retry_pure_zero_default_no_retry() -> None:
    """未配置重试时，失败任务不会再次调度。"""
    assert (
        schedule_retry(status="FAILED", attempt=1, max_retries=0, now=_T0, backoff_sec=60) is None
    )


def test_retry_pure_failed_schedules_fixed_backoff() -> None:
    """FAILED 且剩余次数>0:按部署级固定退避安排(独立 run 行由调用方落库)。"""
    out = schedule_retry(status="FAILED", attempt=1, max_retries=3, now=_T0, backoff_sec=60)
    assert out == _T0 + dt.timedelta(seconds=60)


def test_retry_pure_exhausted_terminal() -> None:
    """次数耗尽仍失败 → 链终态 FAILED:最后一次可用尝试仍安排,超出即终态。"""
    # max_retries=2:attempt=2(首跑+1 重试)失败仍可安排第 2 次重试
    out = schedule_retry(status="FAILED", attempt=2, max_retries=2, now=_T0, backoff_sec=60)
    assert out == _T0 + dt.timedelta(seconds=60)
    # attempt=3(首跑+2 重试)失败即耗尽,不再安排
    assert (
        schedule_retry(status="FAILED", attempt=3, max_retries=2, now=_T0, backoff_sec=60) is None
    )


# ── 集成基建:顺序脚本化目标执行 + 记录型通知器 ──


class _SeqRunner:
    """目标执行替身:按脚本逐次弹出 (error, ok);记录调用参数。"""

    def __init__(self, script: list[tuple[str, bool]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def run_target(
        self,
        *,
        target_type: str,
        target_key: str,
        tenant_id: str,
        tenant_key: str,
        creator_id: str,
        inputs: dict[str, Any],
        channel: str,
        template: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        self.calls.append(
            {
                "creator": creator_id,
                "channel": channel,
                "target": target_key,
                "inputs": dict(inputs),
            }
        )
        err, ok = self._script.pop(0)
        return (ok, f"run-{len(self.calls)}", err)


class _RecNotifier:
    """出站通知替身:记录每次发送(次数与载荷即终态化断言面)。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def check_url(self, url: str) -> None:
        return None

    async def post(self, *, url: str, body: bytes, signature: str) -> None:
        self.calls.append({"url": url, "body": json.loads(body)})


class _NoCipher:
    def encrypt(self, plain: str) -> str:
        return plain

    def decrypt(self, encoded: str) -> str:
        return encoded


def _svc(session: AsyncSession, runner: Any, notifier: Any) -> Any:
    from kongming.trigger.service import TriggerService

    return TriggerService(
        repo=SqlTriggerRepo(session),
        cipher=_NoCipher(),
        runner=runner,
        notifier=notifier,
        retry_backoff_sec=0,  # 集成测试零退避:安排即到期
    )


async def _mk_trigger(
    session: AsyncSession,
    *,
    max_retries: int = 0,
    notify_url: str = "",
    tg_type: str = "CRON",
) -> tuple[str, str]:
    """直插触发器定义行(绕过管理面校验;两表无外键,租户/用户无需 seed)。"""
    repo = SqlTriggerRepo(session)
    tenant_id = str(uuid_mod.uuid4())
    tid = await repo.create(
        tenant_id=tenant_id,
        tg_key=f"rt{uuid_mod.uuid4().hex[:8]}",
        name="重试",
        type=tg_type,
        target_type="workflow",
        target_key="echo",
        cron_expr="0 9 * * *",
        min_interval_sec=60,
        secret_cipher="",
        adapter="custom",
        notify_url=notify_url,
        created_by=str(uuid_mod.uuid4()),
        next_fire_at=None,
        max_retries=max_retries,
    )
    await session.commit()
    return tenant_id, tid


async def _runs_desc(
    session: AsyncSession, tenant_id: str, trigger_id: str
) -> list[dict[str, Any]]:
    repo = SqlTriggerRepo(session)
    _, rows = await repo.page_runs(tenant_id=tenant_id, trigger_id=trigger_id, page=1, size=50)
    return rows


# ── 重试链集成:先败后成 + 耗尽 + 通知终态化 ──


async def test_retry_chain_fail_then_success(session: AsyncSession) -> None:
    """spec「失败后自动重试成功」:首跑 FAILED → 挂起重试行(attempt=2 关联
    首跑)→ 到期派发 SUCCESS → 链终态;中间失败不通知、终态恰通知一次。"""
    tenant_id, tid = await _mk_trigger(session, max_retries=2, notify_url="https://n.example/h")
    runner = _SeqRunner([("外部系统抖动", False), ("", True)])
    notifier = _RecNotifier()
    svc = _svc(session, runner, notifier)
    # 首跑(cron 同路)
    await svc.fire_cron(trigger_id=tid)
    rows = await _runs_desc(session, tenant_id, tid)
    # 按 attempt 定位(零退避同事务落库,created_at 同为事务时间,行序不定)
    assert {(r["attempt"], r["status"]) for r in rows} == {(1, "FAILED"), (2, "PENDING")}
    first = next(r for r in rows if r["attempt"] == 1)
    pending = next(r for r in rows if r["attempt"] == 2)
    assert str(pending["retry_of_run_id"]) == str(first["id"])  # 关联首跑
    assert pending["next_retry_at"] is not None
    assert notifier.calls == []  # 链未终态:通知 0 次
    assert runner.calls[0]["channel"] == "cron"
    # 调度扫描:挂起行(零退避已到期)被扫出并派发(同身份同防御层)
    due = await svc.list_due_retries(now_iso=dt.datetime.now(_TZ).isoformat())
    assert [str(r["id"]) for r in due] == [str(pending["id"])]
    await svc.fire_retry(run_id=str(pending["id"]))
    rows2 = await _runs_desc(session, tenant_id, tid)
    # 挂起行原行落定 SUCCESS(不覆盖首跑失败行)
    assert {(r["attempt"], r["status"]) for r in rows2} == {(1, "FAILED"), (2, "SUCCESS")}
    done = next(r for r in rows2 if r["attempt"] == 2)
    assert str(done["retry_of_run_id"]) == str(first["id"])
    assert done["next_retry_at"] is None  # 终态清除调度态
    assert done["target_run_ref"] == "run-2"
    assert sorted(r["attempt"] for r in rows2) == [1, 2]  # 历史两行,中间失败不抹
    # 链终态成功:通知恰一次(终态成功结果)
    assert len(notifier.calls) == 1
    assert notifier.calls[0]["body"]["status"] == "SUCCESS"
    # SUCCESS 之后不再有挂起行(链终止)
    due2 = await svc.list_due_retries(now_iso=dt.datetime.now(_TZ).isoformat())
    assert [r for r in due2 if str(r["trigger_id"]) == tid] == []


async def test_retry_replays_original_inputs_and_channel(session: AsyncSession) -> None:
    """重试回放:webhook 失败重试以原 inputs 与 webhook 渠道执行
    目标(design「webhook 失败重试同策略适用」),而非空 payload 的 cron 形态
    ——空载荷重试会以无意义执行掩盖真实失败。"""
    tenant_id, tid = await _mk_trigger(
        session, max_retries=2, notify_url="https://n.example/h", tg_type="WEBHOOK"
    )
    runner = _SeqRunner([("目标抖动", False), ("", True)])
    svc = _svc(session, runner, _RecNotifier())
    row = await SqlTriggerRepo(session).get_by_id(trigger_id=tid)
    assert row is not None
    await session.commit()
    # 首跑(webhook 同路):标准化 inputs 随链首行落 summary(回放载体)
    await svc.fire_prepared(row=row, inputs={"event": "push", "repo": "a/b"})
    rows = await _runs_desc(session, tenant_id, tid)
    pending = next(r for r in rows if r["status"] == "PENDING")
    assert runner.calls[0] == {
        "creator": runner.calls[0]["creator"],
        "channel": "webhook",
        "target": "echo",
        "inputs": {"event": "push", "repo": "a/b"},
    }
    # 到期派发:重试以原 inputs+webhook 渠道回放(非空载荷、非 cron 形态)
    await svc.fire_retry(run_id=str(pending["id"]))
    assert len(runner.calls) == 2
    assert runner.calls[1]["channel"] == "webhook"
    assert runner.calls[1]["inputs"] == {"event": "push", "repo": "a/b"}
    rows2 = await _runs_desc(session, tenant_id, tid)
    assert {(r["attempt"], r["status"]) for r in rows2} == {(1, "FAILED"), (2, "SUCCESS")}


async def test_retry_chain_exhausted_terminal_failed(session: AsyncSession) -> None:
    """spec「重试耗尽终态失败」:max_retries=1,首跑+1 重试均 FAILED →
    链终态 FAILED、不再安排、历史保留两行、终态通知一次。"""
    tenant_id, tid = await _mk_trigger(session, max_retries=1, notify_url="https://n.example/h")
    runner = _SeqRunner([("持续失败", False), ("持续失败", False)])
    notifier = _RecNotifier()
    svc = _svc(session, runner, notifier)
    await svc.fire_cron(trigger_id=tid)
    rows = await _runs_desc(session, tenant_id, tid)
    assert {(r["attempt"], r["status"]) for r in rows} == {(1, "FAILED"), (2, "PENDING")}
    assert notifier.calls == []
    await svc.fire_retry(run_id=str(next(r for r in rows if r["attempt"] == 2)["id"]))
    rows2 = await _runs_desc(session, tenant_id, tid)
    assert {(r["attempt"], r["status"]) for r in rows2} == {
        (1, "FAILED"),
        (2, "FAILED"),
    }  # 无 PENDING 残留
    assert len(notifier.calls) == 1  # 耗尽终态:通知一次
    assert notifier.calls[0]["body"]["status"] == "FAILED"
    due = await svc.list_due_retries(now_iso=dt.datetime.now(_TZ).isoformat())
    assert [r for r in due if str(r["trigger_id"]) == tid] == []


async def test_retry_zero_default_single_row_notified(session: AsyncSession) -> None:
    """spec「缺省不重试」:max_retries=0 执行 FAILED = 单行终态+通知一次
    (无挂起行)。"""
    tenant_id, tid = await _mk_trigger(session, max_retries=0, notify_url="https://n.example/h")
    runner = _SeqRunner([("boom", False)])
    notifier = _RecNotifier()
    svc = _svc(session, runner, notifier)
    await svc.fire_cron(trigger_id=tid)
    rows = await _runs_desc(session, tenant_id, tid)
    assert [r["status"] for r in rows] == ["FAILED"]
    assert rows[0]["attempt"] == 1
    assert rows[0]["retry_of_run_id"] is None
    assert len(notifier.calls) == 1  # 无重试链:执行落定即终态,照发通知
    due = await svc.list_due_retries(now_iso=dt.datetime.now(_TZ).isoformat())
    assert [r for r in due if str(r["trigger_id"]) == tid] == []


async def test_retry_defer_keeps_pending(session: AsyncSession) -> None:
    """执行中永不并发:挂起重试派发撞上在途执行 → 顺延一个退避,行保持挂起。"""
    tenant_id, tid = await _mk_trigger(session, max_retries=1, notify_url="")
    runner = _SeqRunner([("boom", False)])
    svc = _svc(session, runner, notifier=None)
    await svc.fire_cron(trigger_id=tid)
    rows = await _runs_desc(session, tenant_id, tid)
    pending = next(r for r in rows if r["attempt"] == 2)
    assert pending["status"] == "PENDING"
    await svc.defer_retry(run_id=str(pending["id"]))
    rows2 = await _runs_desc(session, tenant_id, tid)
    pending2 = next(r for r in rows2 if r["attempt"] == 2)
    assert pending2["status"] == "PENDING"  # 仍挂起(被顺延,未取消未执行)
    assert pending2["next_retry_at"] is not None


# ── 停用/删除取消挂起重试 ──


async def test_retry_disabled_cancels_pending(session: AsyncSession) -> None:
    """spec「停用取消挂起重试」:停用后挂起行收敛终态,到期不再派发;
    重新启用不补做(取消即取消)。"""
    tenant_id, tid = await _mk_trigger(session, max_retries=3, notify_url="")
    svc = _svc(session, _SeqRunner([("boom", False)]), notifier=None)
    await svc.fire_cron(trigger_id=tid)
    rows = await _runs_desc(session, tenant_id, tid)
    pending_id = str(next(r for r in rows if r["attempt"] == 2)["id"])
    assert any(r["status"] == "PENDING" for r in rows)
    await svc.set_enabled(trigger_id=tid, tenant_id=tenant_id, enabled=False)
    rows2 = await _runs_desc(session, tenant_id, tid)
    cancelled = next(r for r in rows2 if r["attempt"] == 2)
    assert cancelled["status"] == "FAILED"  # 挂起取消(收敛终态,可见)
    assert "停用" in str(cancelled["error"])
    assert cancelled["next_retry_at"] is None
    # 到期也不派发(扫描面已无此行)
    due = await svc.list_due_retries(now_iso=dt.datetime.now(_TZ).isoformat())
    assert pending_id not in [str(r["id"]) for r in due]
    # 重新启用不补做:取消了的行不再复活
    await svc.set_enabled(trigger_id=tid, tenant_id=tenant_id, enabled=True)
    due2 = await svc.list_due_retries(now_iso=dt.datetime.now(_TZ).isoformat())
    assert pending_id not in [str(r["id"]) for r in due2]


async def test_retry_deleted_cancels_pending(session: AsyncSession) -> None:
    """spec「删除取消挂起重试」:定义删除后挂起行收敛终态(历史保留)。"""
    tenant_id, tid = await _mk_trigger(session, max_retries=3, notify_url="")
    svc = _svc(session, _SeqRunner([("boom", False)]), notifier=None)
    await svc.fire_cron(trigger_id=tid)
    rows = await _runs_desc(session, tenant_id, tid)
    assert any(r["status"] == "PENDING" for r in rows)
    await svc.delete(trigger_id=tid, tenant_id=tenant_id)
    rows2 = await _runs_desc(session, tenant_id, tid)
    assert next(r for r in rows2 if r["attempt"] == 2)["status"] == "FAILED"
    assert "删除" in str(next(r for r in rows2 if r["attempt"] == 2)["error"])


async def test_retry_fire_skips_when_trigger_disabled(session: AsyncSession) -> None:
    """派发期兜底:停用钩子之外(如竞态窗口)到期重试也不执行,行收敛取消。"""
    tenant_id, tid = await _mk_trigger(session, max_retries=3, notify_url="")
    runner = _SeqRunner([("boom", False)])
    svc = _svc(session, runner, notifier=None)
    await svc.fire_cron(trigger_id=tid)
    rows = await _runs_desc(session, tenant_id, tid)
    pending_id = str(next(r for r in rows if r["attempt"] == 2)["id"])
    await svc.set_enabled(trigger_id=tid, tenant_id=tenant_id, enabled=False)
    # 手工把挂起行放回到期态(模拟取消钩子之外的漏网)
    from sqlalchemy import text as _text

    await session.execute(
        _text(
            "UPDATE public.sys_trigger_run SET status='PENDING',"
            " next_retry_at=now() - interval '1s' WHERE id=CAST(:i AS uuid)"
        ),
        {"i": pending_id},
    )
    await session.commit()
    await svc.fire_retry(run_id=pending_id)
    rows2 = await _runs_desc(session, tenant_id, tid)
    assert next(r for r in rows2 if r["attempt"] == 2)["status"] == "FAILED"  # 不执行、收敛取消
    assert len(runner.calls) == 1  # 目标未再执行(只有首跑那次)


# ── 调度器扫描派发(stub 服务直测核心分支) ──


class _RetryStubService(_StubService):
    """在桩服务上补扫描面:list_due_retries 可注入到期挂起行。"""

    def __init__(
        self, rows: list[dict[str, Any]], due_retries: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(rows)
        self._due_retries = due_retries or []
        self.retried: list[str] = []
        self.deferred: list[str] = []

    async def list_due_retries(self, *, now_iso: str) -> list[dict[str, Any]]:
        return self._due_retries

    async def fire_retry(self, *, run_id: str) -> None:
        self.retried.append(run_id)

    async def defer_retry(self, *, run_id: str) -> None:
        self.deferred.append(run_id)


async def test_scheduler_dispatches_due_retry() -> None:
    """到期挂起重试被调度派发(经 _running 互斥,cron 与重试同保护面)。"""
    svc = _RetryStubService(
        [],
        due_retries=[{"id": "r1", "trigger_id": "t9", "tenant_id": "ten"}],
    )
    sched = _scheduler_with(svc)  # type: ignore[arg-type]
    await sched._tick()
    # 派发是异步任务:等在飞任务收尾再断言
    await asyncio.gather(*sched._running.values())
    assert svc.retried == ["r1"]
    assert svc.deferred == []


async def test_scheduler_defers_retry_when_busy() -> None:
    """cron 执行中重试到期:不并发,挂起行顺延一个退避间隔。"""
    svc = _RetryStubService(
        [],
        due_retries=[{"id": "r2", "trigger_id": "t8", "tenant_id": "ten"}],
    )
    sched = _scheduler_with(svc)  # type: ignore[arg-type]
    sched._running["t8"] = cast(  # 模拟在途(占位 Future 对齐 Task 形态)
        "asyncio.Task[None]", asyncio.get_running_loop().create_future()
    )
    await sched._tick()
    assert svc.retried == []
    assert svc.deferred == ["r2"]


async def test_scheduler_cron_skipped_during_retry_execution() -> None:
    """spec 交织语义「重试执行中 cron 到期记 SKIPPED」:重试占用 _running,
    cron 到期即走既有 SKIPPED 保护(不并发堆积)。"""
    svc = _RetryStubService(
        [
            {
                "id": "t7",
                "tenant_id": "ten",
                "cron_expr": "0 9 * * *",
                "last_fired_at": None,
                "min_interval_sec": 0,
            }
        ],
        due_retries=[],
    )
    sched = _scheduler_with(svc)  # type: ignore[arg-type]
    # 占位 Future 模拟重试执行在途(与既有 in-progress 测试同形态)
    sched._running["t7"] = cast("asyncio.Task[None]", asyncio.get_running_loop().create_future())
    await sched._tick()
    assert svc.fired == []
    assert svc.skipped == ["前次执行进行中"]


# ── 迁移契约:缺省 + CHECK + 部分索引 + 降级往返 ──


async def _column_exists(session: AsyncSession, table: str, column: str) -> bool:
    n = (
        await session.execute(
            text(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_schema='public' AND table_name=:tb AND column_name=:c"
            ),
            {"tb": table, "c": column},
        )
    ).scalar_one()
    return int(n) == 1


# ── 管理面端点扩展(HTTP):max_retries 读写 + 历史重试链字段 ──


async def test_trigger_max_retries_crud_roundtrip(
    client: Any, session: AsyncSession, make_user: Any
) -> None:
    """max_retries 经创建/更新读写回显;越界 422;历史行携带重试链字段。"""
    from kongming.auth.seed import seed_all
    from kongming.config import get_settings
    from tests.test_chat_query import _bind, _login
    from tests.test_toolcall import _mk_role_with_perms, _mk_tenant

    await seed_all(session, get_settings())
    uid = await make_user("rt_admin")
    rid = await _mk_role_with_perms(session, "role_rt_admin", ["system:trigger:manage"])
    tid_tenant = await _mk_tenant(session, "tg")
    await _bind(session, "sys_user_role", uid, rid)
    await _bind(session, "sys_user_tenant", uid, tid_tenant)
    await session.commit()
    # 目标存在性闸:候选目标(创建者=触发器创建者,同口径)
    from tests.test_trigger import _mk_workflow

    await _mk_workflow(session, creator_id=uid)
    token = await _login(client, "rt_admin")
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    # 越界 422(pydantic 值域闸;领域层 is_valid_max_retries 同源兜底)
    r_bad = await client.post(
        "/system/triggers",
        headers=h,
        json={
            "tg_key": "rtbad",
            "name": "越界",
            "type": "CRON",
            "target_type": "workflow",
            "target_key": "echo",
            "cron_expr": "0 9 * * *",
            "max_retries": 6,
        },
    )
    assert r_bad.status_code == 422
    # 合法创建(max_retries=3)→ 列表回显
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={
            "tg_key": "rtcrud",
            "name": "重试",
            "type": "CRON",
            "target_type": "workflow",
            "target_key": "echo",
            "cron_expr": "0 9 * * *",
            "max_retries": 3,
        },
    )
    assert r.status_code == 201, r.text
    lst = await client.get("/system/triggers", headers=h)
    row = next(x for x in lst.json() if x["tg_key"] == "rtcrud")
    assert row["max_retries"] == 3
    # 更新改写(max_retries=0)→ 回显 0
    tid = row["id"]
    r2 = await client.put(
        f"/system/triggers/{tid}",
        headers=h,
        json={
            "name": "重试",
            "target_type": "workflow",
            "target_key": "echo",
            "cron_expr": "0 9 * * *",
            "min_interval_sec": 60,
            "adapter": "custom",
            "notify_url": "",
            "secret": "",
            "max_retries": 0,
        },
    )
    assert r2.status_code == 200, r2.text
    lst2 = await client.get("/system/triggers", headers=h)
    assert next(x for x in lst2.json() if x["tg_key"] == "rtcrud")["max_retries"] == 0
    # 缺省创建(max_retries 省略)→ 0
    r3 = await client.post(
        "/system/triggers",
        headers=h,
        json={
            "tg_key": "rtdefault",
            "name": "缺省",
            "type": "CRON",
            "target_type": "workflow",
            "target_key": "echo",
            "cron_expr": "0 9 * * *",
        },
    )
    assert r3.status_code == 201, r3.text
    lst3 = await client.get("/system/triggers", headers=h)
    assert next(x for x in lst3.json() if x["tg_key"] == "rtdefault")["max_retries"] == 0
    # 历史行携带重试链字段(直插链首+重试行验证端点透出)
    repo = SqlTriggerRepo(session)
    tenant_row = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='tg'"))
    ).scalar_one()
    first_id = await repo.insert_run(
        tenant_id=str(tenant_row),
        trigger_id=tid,
        status="FAILED",
        error="首跑失败",
        duration_ms=10,
        target_run_ref="",
    )
    await repo.insert_run(
        tenant_id=str(tenant_row),
        trigger_id=tid,
        status="SUCCESS",
        error="",
        duration_ms=20,
        target_run_ref="run-2",
        attempt=2,
        retry_of_run_id=first_id,
    )
    await session.commit()
    runs = await client.get(f"/system/triggers/{tid}/runs?page=1&size=10", headers=h)
    items = runs.json()["items"]
    assert sorted(i["attempt"] for i in items) == [1, 2]  # 链两行,行序不依赖
    by_attempt = {i["attempt"]: i for i in items}
    assert by_attempt[2]["retry_of_run_id"] == first_id  # 重试行可追溯首跑
    assert by_attempt[1]["retry_of_run_id"] is None
    assert by_attempt[2]["next_retry_at"] is None  # 终态清除调度态


async def test_migration_0045_defaults_zero_regression(session: AsyncSession) -> None:
    """存量行缺省:不带新列写入 → max_retries=0 / attempt=1 /
    retry_of_run_id 与 next_retry_at 为 NULL。"""
    tid = str(uuid_mod.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_trigger_definition"
            " (id, tenant_id, tg_key, name, type, target_type, target_key, cron_expr,"
            "  created_by) VALUES"
            " (CAST(:i AS uuid), gen_random_uuid(), 'migdef45', '迁移', 'CRON',"
            "  'workflow', 'echo', '0 9 * * *', gen_random_uuid())"
        ),
        {"i": tid},
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_trigger_run"
            " (id, tenant_id, trigger_id, status)"
            " VALUES (gen_random_uuid(), gen_random_uuid(), CAST(:i AS uuid), 'SUCCESS')"
        ),
        {"i": tid},
    )
    await session.commit()
    row = (
        (
            await session.execute(
                text(
                    "SELECT max_retries FROM public.sys_trigger_definition"
                    " WHERE id=CAST(:i AS uuid)"
                ),
                {"i": tid},
            )
        )
        .mappings()
        .one()
    )
    assert row["max_retries"] == 0
    run = (
        (
            await session.execute(
                text(
                    "SELECT attempt, retry_of_run_id, next_retry_at"
                    " FROM public.sys_trigger_run WHERE trigger_id=CAST(:i AS uuid)"
                ),
                {"i": tid},
            )
        )
        .mappings()
        .one()
    )
    assert run["attempt"] == 1
    assert run["retry_of_run_id"] is None
    assert run["next_retry_at"] is None
    # 列契约在位
    assert await _column_exists(session, "sys_trigger_definition", "max_retries")
    assert await _column_exists(session, "sys_trigger_run", "retry_of_run_id")
    assert await _column_exists(session, "sys_trigger_run", "attempt")
    assert await _column_exists(session, "sys_trigger_run", "next_retry_at")
    # 部分索引在位(调度扫描面)
    idx = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_indexes"
                " WHERE schemaname='public' AND tablename='sys_trigger_run'"
                "   AND indexname='ix_sys_trigger_run_retry_due'"
            )
        )
    ).scalar_one()
    assert int(idx) == 1


async def test_migration_0045_check_constraints(session: AsyncSession) -> None:
    """CHECK 契约:max_retries 0–5 之外拒写;run 状态域含 PENDING、域外拒写。"""
    tid = str(uuid_mod.uuid4())
    await session.execute(
        text(
            "INSERT INTO public.sys_trigger_definition"
            " (id, tenant_id, tg_key, name, type, target_type, target_key, cron_expr,"
            "  created_by, max_retries) VALUES"
            " (CAST(:i AS uuid), gen_random_uuid(), 'migck45', '迁移', 'CRON',"
            "  'workflow', 'echo', '0 9 * * *', gen_random_uuid(), 5)"
        ),
        {"i": tid},
    )
    await session.commit()
    with pytest.raises(Exception):  # noqa: B017  # CHECK 违约即抛(DBAPIError)
        await session.execute(
            text(
                "UPDATE public.sys_trigger_definition SET max_retries=6 WHERE id=CAST(:i AS uuid)"
            ),
            {"i": tid},
        )
    await session.rollback()
    # PENDING 入域(挂起重试形态)
    await session.execute(
        text(
            "INSERT INTO public.sys_trigger_run"
            " (id, tenant_id, trigger_id, status, attempt, next_retry_at)"
            " VALUES (gen_random_uuid(), gen_random_uuid(), CAST(:i AS uuid),"
            "  'PENDING', 2, now() + interval '60s')"
        ),
        {"i": tid},
    )
    await session.commit()
    with pytest.raises(Exception):  # noqa: B017
        await session.execute(
            text(
                "INSERT INTO public.sys_trigger_run"
                " (id, tenant_id, trigger_id, status)"
                " VALUES (gen_random_uuid(), gen_random_uuid(), CAST(:i AS uuid), 'BOGUS')"
            ),
            {"i": tid},
        )
    await session.rollback()


async def test_migration_0045_downgrade_roundtrip() -> None:
    """降级往返:up 后 down 再 up,列/约束/缺省契约复现;回 head 不回版本号。"""
    async with migrated_tmp_db() as (session, cfg):
        from alembic import command

        # 往返前置:留一行存量数据,验证再升级后缺省回填(真实「存量行」语义)
        tid = str(uuid_mod.uuid4())
        await session.execute(
            text(
                "INSERT INTO public.sys_trigger_definition"
                " (id, tenant_id, tg_key, name, type, target_type, target_key, cron_expr,"
                "  created_by) VALUES"
                " (CAST(:i AS uuid), gen_random_uuid(), 'migrt45', '迁移', 'CRON',"
                "  'workflow', 'echo', '0 9 * * *', gen_random_uuid())"
            ),
            {"i": tid},
        )
        await session.commit()
        # env.py 内部 asyncio.run:事件循环内直调会冲突,经线程隔离(0034 先例)
        await asyncio.to_thread(command.downgrade, cfg, "0044")
        assert not await _column_exists(session, "sys_trigger_definition", "max_retries")
        assert not await _column_exists(session, "sys_trigger_run", "retry_of_run_id")
        assert not await _column_exists(session, "sys_trigger_run", "attempt")
        assert not await _column_exists(session, "sys_trigger_run", "next_retry_at")
        # 回到当前 head,恢复后续测试需要的完整 schema。
        await asyncio.to_thread(command.upgrade, cfg, "head")
        # 契约复现 + 存量行缺省回填
        assert await _column_exists(session, "sys_trigger_definition", "max_retries")
        assert await _column_exists(session, "sys_trigger_run", "retry_of_run_id")
        assert await _column_exists(session, "sys_trigger_run", "attempt")
        assert await _column_exists(session, "sys_trigger_run", "next_retry_at")
        row = (
            (
                await session.execute(
                    text(
                        "SELECT max_retries FROM public.sys_trigger_definition"
                        " WHERE id=CAST(:i AS uuid)"
                    ),
                    {"i": tid},
                )
            )
            .mappings()
            .one()
        )
        assert row["max_retries"] == 0
