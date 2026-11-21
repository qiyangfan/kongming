"""覆盖触发器的 cron、签名、调度和目标执行。"""
# pyright: reportPrivateUsage=false

import asyncio
import datetime as dt
import hashlib
import hmac as hmac_mod
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.trigger.adapters import AdapterError, normalize
from kongming.trigger.models import CronError, next_fire, parse_cron
from tests.conftest import MakeUser
from tests.test_chat_query import _bind, _login
from tests.test_toolcall import _mk_role_with_perms, _mk_tenant

_TZ = dt.timezone(dt.timedelta(hours=8))


# ── cron 解析(纯函数) ──


def test_cron_all_forms() -> None:
    m, h, dom, _mon, dow = parse_cron("*/15 9-17 * * 1-5")
    assert 0 in m and 15 in m and 45 in m
    assert 9 in h and 17 in h and 8 not in h
    assert 1 in dom and 31 in dom  # *=全域
    assert dow == {1, 2, 3, 4, 5}
    # 列表形态单独验
    assert parse_cron("0 9 1,15 * *")[2] == {1, 15}


def test_cron_rejects() -> None:
    for bad in (
        "",
        "0 9 * *",
        "0 9 * * * *",
        "61 * * * *",
        "0 9 1 * 1",
        "a b c d e",
        "0 9 * * 8",
        "*/0 * * * *",
        "5-1 * * * *",
    ):
        with pytest.raises(CronError):
            parse_cron(bad)


def test_cron_next_fire() -> None:
    after = dt.datetime(2026, 8, 24, 8, 59, 30, tzinfo=_TZ)  # 周一
    assert next_fire("0 9 * * *", after) == dt.datetime(2026, 8, 24, 9, 0, tzinfo=_TZ)
    # 月末:1 月 31 日的「31 日 0 点」→ 2 月无 31 日,跳到 3 月 31
    feb = dt.datetime(2026, 2, 1, 0, 0, tzinfo=_TZ)
    assert next_fire("0 0 31 * *", feb) == dt.datetime(2026, 3, 31, 0, 0, tzinfo=_TZ)
    # 周日 0/7 等价
    sat = dt.datetime(2026, 8, 29, 12, 0, tzinfo=_TZ)  # 周六
    assert next_fire("0 0 * * 0", sat) == dt.datetime(2026, 8, 30, 0, 0, tzinfo=_TZ)
    assert next_fire("0 0 * * 7", sat) == dt.datetime(2026, 8, 30, 0, 0, tzinfo=_TZ)


# ── 适配器 ──


def test_adapter_custom_passthrough() -> None:
    assert normalize("custom", {"a": 1}) == {"a": 1}


def test_adapter_github_push() -> None:
    # 空 dict/嵌套混合使推断带 dict[Unknown, Unknown],显式 Any 值域
    payload: dict[str, Any] = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "org/repo"},
        "commits": [{}, {}],
        "pusher": {"name": "qiuye"},
    }
    out = normalize("github", payload, "push")
    assert out["event"] == "push" and out["repo"] == "org/repo"
    assert out["ref"] == "refs/heads/main" and "2 commits" in out["summary"]


def test_adapter_github_pr_payload_absent() -> None:
    """PR 事件但提载体缺失:ref/summary 均为空(不拼「PR # 」空壳)。"""
    payload: dict[str, Any] = {"repository": {"full_name": "org/repo"}, "pull_request": None}
    out = normalize("github", payload, "pull_request")
    assert out["ref"] == "" and out["summary"] == ""


def test_adapter_github_missing_event() -> None:
    with pytest.raises(AdapterError):
        normalize("github", {}, "")


def test_adapter_unknown() -> None:
    with pytest.raises(AdapterError):
        normalize("slack", {})


# ── 调度器(stub 服务直测核心分支) ──


class _StubService:
    """调度器消费的全部方法内存实现。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.fired: list[str] = []
        self.skipped: list[str] = []
        self.failed: list[str] = []
        self.marked: list[tuple[str, str | None]] = []
        # 重试扫描面(缺省无挂起行;专项断言见 test_trigger_retry.py)
        self.retried: list[str] = []
        self.deferred: list[str] = []

    async def list_due(self, *, now_iso: str) -> list[dict[str, Any]]:
        return self._rows

    async def list_due_retries(self, *, now_iso: str) -> list[dict[str, Any]]:
        return []

    async def fire_retry(self, *, run_id: str) -> None:
        self.retried.append(run_id)

    async def defer_retry(self, *, run_id: str) -> None:
        self.deferred.append(run_id)

    # 邮件轮询扫描面(缺省无到期邮件触发器;专项断言见 test_trigger_email.py)
    async def list_due_emails(self, *, now_iso: str) -> list[dict[str, Any]]:
        return []

    async def poll_email(self, *, trigger_id: str) -> None:
        return None

    async def mark_email_polled(self, *, trigger_id: str, interval_sec: int) -> None:
        return None

    async def fire_cron(self, *, trigger_id: str) -> None:
        self.fired.append(trigger_id)

    async def get_def(self, *, trigger_id: str) -> dict[str, Any] | None:
        return next((r for r in self._rows if r["id"] == trigger_id), None)

    async def mark_fired(
        self, *, trigger_id: str, last_fired_iso: str, next_fire_iso: str | None
    ) -> None:
        self.marked.append((trigger_id, next_fire_iso))

    async def record_skipped(self, *, tenant_id: str, trigger_id: str, reason: str) -> None:
        self.skipped.append(reason)

    async def record_failed(self, *, tenant_id: str, trigger_id: str, reason: str) -> None:
        self.failed.append(reason)


def _scheduler_with(svc: _StubService):
    from kongming.trigger.scheduler import TriggerScheduler

    async def _factory():
        async def _close() -> None:
            return None

        return svc, _close

    sched = TriggerScheduler(service_factory=None)
    # 桩服务与 TriggerService 鸭子类型相容,cast 对齐工厂协议(运行时不变)
    sched._factory = cast(  # 测试注入(与 lifespan 同点)
        "Callable[[], Awaitable[tuple[Any, Callable[[], Awaitable[None]]]]]",
        _factory,
    )
    return sched


async def test_scheduler_min_interval_skips() -> None:
    """spec「最小间隔保护」:刚触发过的行记 SKIPPED 且不执行。"""
    recent = dt.datetime.now(dt.UTC)
    svc = _StubService(
        [
            {
                "id": "t1",
                "tenant_id": "ten",
                "cron_expr": "0 9 * * *",
                "last_fired_at": recent,
                "min_interval_sec": 60,
            }
        ]
    )
    sched = _scheduler_with(svc)
    await sched._tick()  # 直测 tick(测试专用入口)
    assert svc.fired == []
    assert svc.skipped == ["最小间隔保护"]
    assert svc.marked and svc.marked[0][1] is not None  # SKIPPED 也推进 next_fire


async def test_scheduler_in_progress_skips() -> None:
    """spec「并发不堆积」:进行中再到期记 SKIPPED。"""
    svc = _StubService(
        [
            {
                "id": "t2",
                "tenant_id": "ten",
                "cron_expr": "0 9 * * *",
                "last_fired_at": None,
                "min_interval_sec": 0,
            }
        ]
    )
    sched = _scheduler_with(svc)
    # 占位 Future 模拟在途执行,cast 对齐 Task 形态(运行时不变)
    sched._running["t2"] = cast("asyncio.Task[None]", asyncio.get_running_loop().create_future())
    await sched._tick()
    assert svc.fired == []
    assert svc.skipped == ["前次执行进行中"]


async def test_scheduler_cron_recalc_failure_stops() -> None:
    """回归:cron 重算失败 → 落 FAILED+next_fire 置 NULL(止损),
    不吞成「不推进」触发风暴。"""
    svc = _StubService(
        [
            {
                "id": "t3",
                "tenant_id": "ten",
                "cron_expr": "0 0 29 2 *",
                "last_fired_at": None,
                "min_interval_sec": 0,
            }
        ]
    )
    sched = _scheduler_with(svc)
    import kongming.trigger.models as models_mod
    from kongming.trigger.models import CronError

    def _boom(*a: object, **k: object) -> None:
        raise CronError("366 天内无触发时刻")

    orig = models_mod.next_fire
    models_mod.next_fire = _boom
    try:
        await sched._fire_then_mark("t3")
    finally:
        models_mod.next_fire = orig
    assert svc.failed and "下次触发计算失败" in svc.failed[0]
    assert svc.fired == ["t3"]  # 目标已执行(执行与止损独立)
    assert svc.marked and svc.marked[0][1] is None  # next_fire 置 NULL 止损


async def test_webhook_disabled_and_oversize(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """S4 缺口:禁用 webhook 401;body 超限 413。"""
    uid_ds, token = await _trigger_admin(client, session, make_user, "tg_ds1")
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    await _mk_workflow(session, creator_id=uid_ds)  # 目标存在性闸对齐
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={
            "tg_key": "dshook",
            "name": "禁",
            "type": "WEBHOOK",
            "target_type": "workflow",
            "target_key": "echo",
            "secret": "s3cr",
        },
    )
    path = r.json()["webhook_path"]
    tid = r.json()["id"]
    await client.post(f"/system/triggers/{tid}/disable", headers=h)
    payload = b'{"a":1}'
    sig = hmac_mod.new(b"s3cr", payload, hashlib.sha256).hexdigest()
    r2 = await client.post(f"/webhooks/{path}", content=payload, headers={"X-Signature": sig})
    assert r2.status_code == 401
    r3 = await client.post(
        f"/webhooks/{path}",
        content=b"x" * (1024 * 1024 + 1),
        headers={"X-Signature": sig},
    )
    assert r3.status_code == 413


# ── 管理面与入站(HTTP) ──


async def _trigger_admin(
    client: AsyncClient, s: AsyncSession, make_user: MakeUser, name: str
) -> tuple[str, str]:
    await seed_all(s, get_settings())
    uid = await make_user(name)
    rid = await _mk_role_with_perms(s, f"role_{name}", ["system:trigger:manage"])
    tid = await _mk_tenant(s, "tg")
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, tid)
    await s.commit()
    return uid, _login(uid)


async def _mk_workflow(
    s: AsyncSession, tenant_key: str = "tg", creator_id: str | None = None
) -> None:
    """目标工作流(transform 单节点,零外部依赖)。
    creator_id:归属可见性——个人资源仅创建者可见,触发器以创建者
    身份执行,工作流的创建者须与触发器创建者一致(否则 404 同形)。"""

    tid = (
        await s.execute(
            text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"), {"k": tenant_key}
        )
    ).scalar_one()
    if creator_id is None:
        creator_id = (await s.execute(text("SELECT id FROM public.sys_user LIMIT 1"))).scalar_one()
    uid = creator_id
    await s.execute(
        text(
            "INSERT INTO public.sys_workflow_def"
            " (id, tenant_id, wf_key, name, version, definition, created_by)"
            " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'echo', '回显', 1,"
            "         CAST(:d AS jsonb), CAST(:u AS uuid))"
        ),
        {
            "t": str(tid),
            "u": str(uid),
            "d": json.dumps(
                {"nodes": [{"id": "t", "type": "transform", "config": {"template": "{{inputs}}"}}]}
            ),
        },
    )
    await s.commit()


_CRON_BODY = {
    "type": "CRON",
    "target_type": "workflow",
    "target_key": "echo",
    "cron_expr": "0 9 * * *",
}


async def test_trigger_crud_and_cron_validation(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    uid, token = await _trigger_admin(client, session, make_user, "tg_admin1")
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    # 非法 cron 400
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={**_CRON_BODY, "tg_key": "bad", "name": "坏", "cron_expr": "61 * * * *"},
    )
    assert r.status_code == 400
    await _mk_workflow(session, creator_id=uid)  # 目标存在性闸对齐
    # 合法创建,next_fire_at 预计算
    r2 = await client.post(
        "/system/triggers",
        headers=h,
        json={**_CRON_BODY, "tg_key": "daily", "name": "日报"},
    )
    assert r2.status_code == 201, r2.text
    lst = await client.get("/system/triggers", headers=h)
    row = next(x for x in lst.json() if x["tg_key"] == "daily")
    assert row["next_fire_at"] is not None
    assert row["has_secret"] is False
    # 启停
    r3 = await client.post(f"/system/triggers/{row['id']}/disable", headers=h)
    assert r3.status_code == 200
    lst2 = await client.get("/system/triggers", headers=h)
    assert next(x for x in lst2.json() if x["tg_key"] == "daily")["enabled"] is False


async def test_trigger_perm_and_isolation(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    _, _token = await _trigger_admin(client, session, make_user, "tg_admin2")
    uid2 = await make_user("tg_noperm")
    from tests.test_chat_query import _default_tenant_id

    await _bind(session, "sys_user_tenant", uid2, await _default_tenant_id(session))
    await session.commit()
    token2 = _login(client)
    r = await client.get("/system/triggers", headers={"Authorization": f"Bearer {token2}"})
    assert r.status_code == 403


async def test_webhook_signature_matrix(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec 签名矩阵:正确 202+异步触发落 run;错误/缺失/不存在同形拒。"""
    uid_wh1, token = await _trigger_admin(client, session, make_user, "tg_wh1")
    await _mk_workflow(session, creator_id=uid_wh1)
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={
            "tg_key": "hook",
            "name": "钩子",
            "type": "WEBHOOK",
            "target_type": "workflow",
            "target_key": "echo",
            "secret": "topsecret123",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    path = body["webhook_path"]
    assert body["secret"] == "topsecret123"  # 创建一次性展示
    # 列表不回显明文
    lst = await client.get("/system/triggers", headers=h)
    assert "topsecret123" not in lst.text
    assert next(x for x in lst.json() if x["tg_key"] == "hook")["has_secret"] is True

    payload = json.dumps({"msg": "hi"}).encode()
    sig = hmac_mod.new(b"topsecret123", payload, hashlib.sha256).hexdigest()
    r2 = await client.post(
        f"/webhooks/{path}",
        content=payload,
        headers={"X-Signature": sig, "Content-Type": "application/json"},
    )
    assert r2.status_code == 202
    # 异步执行落 run(轮询等待)
    import asyncio

    tid_row = (
        await session.execute(
            text("SELECT id FROM public.sys_trigger_definition WHERE tg_key='hook'")
        )
    ).scalar_one()
    status = None
    for _ in range(20):
        await session.commit()  # 刷新可见性(独立会话写入)
        status = (
            await session.execute(
                text(
                    "SELECT status FROM public.sys_trigger_run"
                    " WHERE trigger_id=CAST(:g AS uuid) ORDER BY created_at DESC LIMIT 1"
                ),
                {"g": str(tid_row)},
            )
        ).scalar_one_or_none()
        if status:
            break
        await asyncio.sleep(0.3)
    assert status == "SUCCESS"
    # 签名错误 401
    r3 = await client.post(
        f"/webhooks/{path}", content=payload, headers={"X-Signature": "deadbeef"}
    )
    assert r3.status_code == 401
    # 缺失 401;不存在路径 404(不区分语义)
    r4 = await client.post(f"/webhooks/{path}", content=payload)
    assert r4.status_code == 401
    import uuid as _u

    r5 = await client.post(f"/webhooks/{_u.uuid4()}", content=payload, headers={"X-Signature": sig})
    assert r5.status_code in (401, 404)


async def test_webhook_github_adapter_normalizes(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """spec「GitHub push 标准化」:事件提要进目标输入。"""
    uid_gh1, token = await _trigger_admin(client, session, make_user, "tg_gh1")
    await _mk_workflow(session, creator_id=uid_gh1)
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={
            "tg_key": "ghhook",
            "name": "GH",
            "type": "WEBHOOK",
            "target_type": "workflow",
            "target_key": "echo",
            "secret": "ghsecret",
            "adapter": "github",
        },
    )
    path = r.json()["webhook_path"]
    payload = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {"full_name": "o/r"},
            "commits": [{}],
            "pusher": {"name": "qiuye"},
        }
    ).encode()
    sig = hmac_mod.new(b"ghsecret", payload, hashlib.sha256).hexdigest()
    r2 = await client.post(
        f"/webhooks/{path}",
        content=payload,
        headers={"X-Signature": sig, "X-GitHub-Event": "push"},
    )
    assert r2.status_code == 202


# ============ 目标存在性闸 ============


async def test_trigger_unknown_target_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """目标不存在:create 400 TARGET_NOT_FOUND,不落库不进调度(写边界闸)。"""
    _, token = await _trigger_admin(client, session, make_user, "tg_040a")
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={**_CRON_BODY, "tg_key": "ghost040", "name": "悬空目标"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "TARGET_NOT_FOUND"
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_trigger_definition WHERE tg_key='ghost040'")
        )
    ).scalar_one()
    assert n == 0


async def test_trigger_update_change_target_revalidates(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """update 改指不存在目标:400 TARGET_NOT_FOUND,原值不变。"""
    uid, token = await _trigger_admin(client, session, make_user, "tg_040b")
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    await _mk_workflow(session, creator_id=uid)
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={**_CRON_BODY, "tg_key": "upd040", "name": "日报"},
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    r2 = await client.put(
        f"/system/triggers/{tid}",
        headers=h,
        json={
            "name": "日报",
            "target_type": "workflow",
            "target_key": "nope040",
            "cron_expr": "0 9 * * *",
            "min_interval_sec": 60,
            "adapter": "custom",
            "notify_url": "",
            "secret": "",
        },
    )
    assert r2.status_code == 400, r2.text
    assert r2.json()["detail"] == "TARGET_NOT_FOUND"
    row = (
        await session.execute(
            text("SELECT target_key FROM public.sys_trigger_definition WHERE tg_key='upd040'")
        )
    ).scalar_one()
    assert row == "echo"  # 原值不变


async def test_trigger_orphan_still_manageable(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """目标已删的存量触发器:停用/改非目标字段/删除均放行(止损路径不堵)。"""
    uid, token = await _trigger_admin(client, session, make_user, "tg_040c")
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    await _mk_workflow(session, creator_id=uid)
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={**_CRON_BODY, "tg_key": "orphan040", "name": "孤儿"},
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    await session.execute(
        text(
            "DELETE FROM public.sys_workflow_def WHERE wf_key='echo' AND tenant_id="
            "(SELECT id FROM public.sys_tenant WHERE tenant_key='tg')"
        )
    )
    await session.commit()
    r2 = await client.post(f"/system/triggers/{tid}/disable", headers=h)
    assert r2.status_code == 200
    r3 = await client.put(
        f"/system/triggers/{tid}",
        headers=h,
        json={
            "name": "改名",
            "target_type": "workflow",
            "target_key": "echo",
            "cron_expr": "0 9 * * *",
            "min_interval_sec": 60,
            "adapter": "custom",
            "notify_url": "",
            "secret": "",
        },
    )
    assert r3.status_code == 200  # 目标未变:不重校验,止损不被堵
    r4 = await client.delete(f"/system/triggers/{tid}", headers=h)
    assert r4.status_code == 200


# ============ cron 预览 ============


async def test_cron_preview_three_fires(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """合法表达式:返回按时间递增的 3 次触发时刻(Asia/Shanghai)。"""
    _, token = await _trigger_admin(client, session, make_user, "tg_040pv")
    r = await client.get(
        "/system/triggers/cron-preview",
        params={"expr": "0 9 * * *"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"},
    )
    assert r.status_code == 200, r.text
    fires = r.json()["next"]
    assert len(fires) == 3
    assert fires == sorted(fires)  # 时间递增
    assert len(set(fires)) == 3  # 无重复


async def test_cron_preview_invalid_400(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """非法表达式:400 附可读原因(与创建期校验同口径)。"""
    _, token = await _trigger_admin(client, session, make_user, "tg_040pv2")
    r = await client.get(
        "/system/triggers/cron-preview",
        params={"expr": "61 * * * *"},
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"},
    )
    assert r.status_code == 400
    assert "越界" in r.json()["detail"]  # 61 越分钟域上界,与创建期同文案


async def test_trigger_team_workflow_target_allowed(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    """团队成员可以触发其他成员创建的 workflow。
    候选可选,存在性闸必须同口径放行——checker 缺 team_ids 时创建必 400。"""
    uid, token = await _trigger_admin(client, session, make_user, "tg_040team")
    h = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tg"}
    tid = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='tg'"))
    ).scalar_one()
    # 他人创建的团队 workflow + uid 为团队成员(MEMBER):
    # created_by 取 seed_all 播的 admin(≠触发器创建者),确保只靠 team 口径可见
    owner_id = (
        await session.execute(text("SELECT id FROM public.sys_user WHERE username='admin' LIMIT 1"))
    ).scalar_one()
    await session.execute(
        text(
            "DELETE FROM public.sys_workflow_def WHERE wf_key='teamecho040'"
            " AND tenant_id=CAST(:t AS uuid)"
        ),
        {"t": str(tid)},
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_team (id, tenant_id, team_key, name, created_by)"
            " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'tm040', '组',"
            "         CAST(:o AS uuid))"
            " ON CONFLICT (tenant_id, team_key) DO NOTHING"
        ),
        {"t": str(tid), "o": str(owner_id)},
    )
    team_id = (
        await session.execute(
            text(
                "SELECT id FROM public.sys_team"
                " WHERE tenant_id=CAST(:t AS uuid) AND team_key='tm040'"
            ),
            {"t": str(tid)},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO public.sys_team_member (team_id, user_id, role)"
            " VALUES (CAST(:tm AS uuid), CAST(:u AS uuid), 'MEMBER')"
            " ON CONFLICT (team_id, user_id) DO NOTHING"
        ),
        {"tm": str(team_id), "u": str(uid)},
    )
    await session.execute(
        text(
            "INSERT INTO public.sys_workflow_def"
            " (id, tenant_id, wf_key, name, version, definition, created_by, team_id)"
            " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'teamecho040', '团队回显', 1,"
            "         CAST(:d AS jsonb), CAST(:o AS uuid), CAST(:tm AS uuid))"
        ),
        {
            "t": str(tid),
            "o": str(owner_id),
            "tm": str(team_id),
            "d": json.dumps(
                {"nodes": [{"id": "t", "type": "transform", "config": {"template": "{{inputs}}"}}]}
            ),
        },
    )
    await session.commit()
    r = await client.post(
        "/system/triggers",
        headers=h,
        json={**_CRON_BODY, "tg_key": "team040", "name": "团队目标", "target_key": "teamecho040"},
    )
    assert r.status_code == 201, r.text  # 团队成员挂团队 workflow:闸不得误拒
