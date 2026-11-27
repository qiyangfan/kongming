"""控制面消费者回归:快照验签/版本/安全闸、命令、用量、注册、只读投影。

外部控制面一律用 httpx.MockTransport;不发起真实网络。
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import Settings, get_settings
from kongming.control.catalog import CatalogService
from kongming.control.client import ControlPlaneClient
from kongming.control.commands import CommandExecutor
from kongming.control.dependencies import (
    get_control_runtime,
    install_control_runtime,
)
from kongming.control.entitlement import EntitlementService, QuotaLimits
from kongming.control.events import install_event_sink, report_product_event
from kongming.control.identity import StaticTokenProvider
from kongming.control.models import (
    CatalogEntryRecord,
    CommandOutcome,
    ControlSnapshot,
    EntitlementRecord,
    FetchResult,
    HealthStatus,
    MembershipRecord,
    ProductEvent,
    SnapshotRejected,
    SnapshotSections,
    TenantRecord,
)
from kongming.control.payload import parse_snapshot, serialize_sections, sign
from kongming.control.registration import RegistrationService
from kongming.control.snapshot import SnapshotService, SystemClock
from kongming.control.store import SqlCommandReceiptStore
from kongming.control.usage import ProductEventRejected, UsageReporter, build_product_event
from kongming.db import get_sessionmaker, uuid7
from tests.conftest import MakeUser

SECRET = "test-signing-secret"  # 测试预共享验签密钥


def _sections(
    *, tenant_key: str = "default", bot_key: str | None = None, with_entitlement: bool = True
) -> SnapshotSections:
    tenant_id = str(uuid7())
    entitlements = (
        (
            EntitlementRecord(
                tenant_id=tenant_id,
                tenant_key=tenant_key,
                quota_calls_daily=10,
                quota_calls_monthly=100,
                quota_bot_calls_daily=5,
                entitlement_keys=("store.basic",),
            ),
        )
        if with_entitlement
        else ()
    )
    catalog = (
        (
            CatalogEntryRecord(
                bot_key=bot_key,
                listing="LISTED",
                visibility="TENANTS",
                tenant_keys=(tenant_key,),
                entitlement_key="store.basic",
            ),
        )
        if bot_key
        else ()
    )
    return SnapshotSections(
        tenants=(TenantRecord(tenant_id=tenant_id, tenant_key=tenant_key, tenant_name="默认"),),
        members=(),
        coarse_roles=(),
        entitlements=entitlements,
        policy_packages=(),
        catalog=catalog,
    )


def _payload(
    version: int, sections: SnapshotSections | None = None, **over: object
) -> dict[str, object]:
    secs = serialize_sections(sections or _sections())
    data: dict[str, object] = {
        "service_id": "kongming",
        "version": version,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "etag": f"v{version}",
        "sections": secs,
    }
    data["signature"] = sign(
        secret=SECRET,
        service_id="kongming",
        version=cast(int, data["version"]),
        observed_at=cast(str, data["observed_at"]),
        etag=cast(str, data["etag"]),
        sections=secs,
    )
    data.update(over)
    return data


class _FakeSource:
    def __init__(self, payloads: list[dict[str, object] | Exception]) -> None:
        self._items = list(payloads)
        self.calls: list[str] = []

    async def fetch(self, *, service_id: str, etag: str) -> FetchResult:
        self.calls.append(etag)
        item = self._items.pop(0) if self._items else Exception("no more")
        if isinstance(item, Exception):
            raise item
        return FetchResult(not_modified=False, etag=str(item["etag"]), payload=item)


class _MemStore:
    def __init__(self) -> None:
        self.snapshot: ControlSnapshot | None = None

    async def load(self, *, service_id: str) -> ControlSnapshot | None:
        return self.snapshot

    async def save(self, snapshot: ControlSnapshot) -> bool:
        if self.snapshot is not None and snapshot.version <= self.snapshot.version:
            return False
        self.snapshot = snapshot
        return True


class _Applier:
    def __init__(self) -> None:
        self.applied: list[int] = []

    async def apply(self, sections: SnapshotSections) -> None:
        self.applied.append(len(sections.tenants))


def _snapshot_service(source: _FakeSource, store: _MemStore, applier: _Applier) -> SnapshotService:
    return SnapshotService(
        source=source,
        store=store,
        applier=applier,
        clock=SystemClock(),
        service_id="kongming",
        signing_secret=SECRET,
        stale_after_sec=300,
        enabled=True,
    )


# ── 快照:验签/版本/安全闸 ──


def test_snapshot_signature_and_security_sections() -> None:
    payload = _payload(1)
    snap = parse_snapshot(payload, expected_service_id="kongming", secret=SECRET)
    assert snap.version == 1
    bad = dict(payload)
    bad["sections"] = {**cast(dict[str, object], payload["sections"])}
    with pytest.raises(SnapshotRejected, match="签名"):
        parse_snapshot({**bad, "etag": "drift"}, expected_service_id="kongming", secret=SECRET)
    missing = cast(dict[str, object], payload["sections"]).copy()
    missing.pop("entitlements")
    with pytest.raises(SnapshotRejected, match="安全节"):
        parse_snapshot(
            {**payload, "sections": missing}, expected_service_id="kongming", secret=SECRET
        )


async def test_snapshot_version_monotonic_and_last_known() -> None:
    store = _MemStore()
    applier = _Applier()
    source = _FakeSource([_payload(2), _payload(1), _payload(2), ConnectionError("down")])
    svc = _snapshot_service(source, store, applier)
    assert await svc.refresh() == "updated"
    assert (await svc.view()).version == 2
    assert await svc.refresh() == "rejected"  # 旧版本不覆盖新版本
    assert await svc.refresh() == "not_modified"
    assert await svc.refresh() == "unavailable"  # 控制面缺席保留 last-known
    view = await svc.view()
    assert view.present and view.version == 2
    assert applier.applied == [1]


async def test_security_missing_fail_closed() -> None:
    svc = _snapshot_service(_FakeSource([]), _MemStore(), _Applier())
    with pytest.raises(Exception, match="无有效安全类快照"):
        await svc.require_security()


async def test_snapshot_stale_marking() -> None:
    store = _MemStore()
    svc = _snapshot_service(_FakeSource([_payload(1)]), store, _Applier())
    await svc.refresh()
    store.snapshot = ControlSnapshot(
        service_id="kongming",
        version=1,
        observed_at=datetime.now(UTC) - timedelta(hours=1),
        sections=_sections(),
    )
    svc2 = _snapshot_service(_FakeSource([]), store, _Applier())
    assert (await svc2.view()).stale is True


# ── 身份缓存 ──


async def test_identity_cache_applies_and_reconciles(session: AsyncSession) -> None:
    from kongming.control.identity_cache import SqlIdentityApplier

    await seed_all(session, get_settings())
    uid = str(uuid7())
    tid = str(uuid7())
    sections = SnapshotSections(
        tenants=(TenantRecord(tenant_id=tid, tenant_key="t1", tenant_name="租户一"),),
        members=(
            MembershipRecord(
                user_id=uid,
                username="control-user",
                nickname="控制用户",
                email=None,
                status="ACTIVE",
                tenant_key="t1",
                coarse_role_keys=("DEVELOPER",),
            ),
        ),
        coarse_roles=(),
        entitlements=(),
        policy_packages=(),
    )
    await SqlIdentityApplier(get_sessionmaker()).apply(sections)
    row = (
        await session.execute(
            text(
                "SELECT u.username, t.tenant_key FROM public.sys_user u"
                " JOIN public.sys_user_tenant ut ON ut.user_id = u.id"
                " JOIN public.sys_tenant t ON t.id = ut.tenant_id"
                " WHERE u.id = CAST(:u AS uuid)"
            ),
            {"u": uid},
        )
    ).one()
    assert row[0] == "control-user" and row[1] == "t1"
    # 快照移除成员 → 该租户下成员关系被回收
    await SqlIdentityApplier(get_sessionmaker()).apply(
        SnapshotSections(tenants=sections.tenants, members=(), coarse_roles=(), entitlements=())
    )
    left = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_user_tenant WHERE user_id = CAST(:u AS uuid)"),
            {"u": uid},
        )
    ).scalar_one()
    assert int(left) == 0


# ── 命令执行器 ──


async def _receipts() -> SqlCommandReceiptStore:
    return SqlCommandReceiptStore(get_sessionmaker())


async def test_command_rejects_unknown_action_and_params() -> None:
    calls: list[dict[str, object]] = []

    async def ok(params: dict[str, object], *, actor: str) -> dict[str, object]:
        calls.append(params)
        return {"ok": True}

    executor = CommandExecutor(
        receipts=await _receipts(),
        handlers={"rag.reindex": ok},
        audit=cast(Any, _NullAudit()),
    )
    unknown = await executor.execute(
        command_id="c1",
        idempotency_key="k1",
        action="shell.exec",
        action_version=1,
        params={"cmd": "rm"},
        actor="a",
        source="control",
    )
    assert unknown.status == "REJECTED"
    bad_version = await executor.execute(
        command_id="c2",
        idempotency_key="k2",
        action="rag.reindex",
        action_version=9,
        params={"tenant_key": "t", "datasource_key": "d"},
        actor="a",
        source="control",
    )
    assert bad_version.status == "REJECTED"
    bad_params = await executor.execute(
        command_id="c3",
        idempotency_key="k3",
        action="rag.reindex",
        action_version=1,
        params={"tenant_key": "t", "datasource_key": "d", "sql": "DROP"},
        actor="a",
        source="control",
    )
    assert bad_params.status == "REJECTED" and not calls


async def test_command_idempotency() -> None:
    calls: list[int] = []

    async def ok(params: dict[str, object], *, actor: str) -> dict[str, object]:
        calls.append(1)
        return {"n": len(calls)}

    executor = CommandExecutor(
        receipts=await _receipts(),
        handlers={"cache.clear": ok},
        audit=cast(Any, _NullAudit()),
    )
    first = await executor.execute(
        command_id="c",
        idempotency_key="same-key",
        action="cache.clear",
        action_version=1,
        params={"tenant_key": "t", "datasource_key": "d"},
        actor="a",
        source="control",
    )
    second = await executor.execute(
        command_id="c",
        idempotency_key="same-key",
        action="cache.clear",
        action_version=1,
        params={"tenant_key": "t", "datasource_key": "d"},
        actor="a",
        source="control",
    )
    assert first.status == "SUCCEEDED" and len(calls) == 1
    assert second.status == "SUCCEEDED"


class _NullAudit:
    async def log(
        self, *, actor: str | None, action: str, target: str, detail: dict[str, object]
    ) -> None:
        return None


# ── 用量 ──


def test_product_event_rejects_llm_and_content_fields() -> None:
    cases: tuple[dict[str, object], ...] = (
        {"tokens": 1},
        {"cost": 1},
        {"prompt": "x"},
        {"result": "x"},
        {"api_key": "x"},
    )
    for attrs in cases:
        with pytest.raises(ProductEventRejected):
            build_product_event(
                event_id="e",
                event_type="chat.turn",
                occurred_at=datetime.now(UTC),
                tenant_id="t",
                attributes=attrs,
            )
    ok = build_product_event(
        event_id="e",
        event_type="chat.turn",
        occurred_at=datetime.now(UTC),
        tenant_id="t",
        attributes={"status": "SUCCESS"},
    )
    assert ok.attributes == {"status": "SUCCESS"}


class _MemOutbox:
    def __init__(self) -> None:
        self.items: list[ProductEvent] = []
        self.delivered_ids: list[str] = []

    async def add(self, event: ProductEvent) -> None:
        self.items.append(event)

    async def pending(self, *, limit: int) -> list[ProductEvent]:
        return [e for e in self.items if e.event_id not in self.delivered_ids][:limit]

    async def delivered(self, *, event_ids: list[str]) -> None:
        self.delivered_ids.extend(event_ids)

    async def failed(self, *, event_ids: list[str], next_attempt_at: datetime) -> None:
        return None

    async def count_pending(self) -> int:
        return len([e for e in self.items if e.event_id not in self.delivered_ids])

    async def trim_to(self, *, limit: int) -> int:
        return 0


class _Sink:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[list[ProductEvent]] = []
        self.fail = fail

    async def send(self, events: list[ProductEvent]) -> None:
        if self.fail:
            raise ConnectionError("down")
        self.sent.append(events)


async def test_usage_flush_and_failure_buffer() -> None:
    outbox = _MemOutbox()
    sink = _Sink(fail=True)
    reporter = UsageReporter(
        outbox=cast(Any, outbox), sink=sink, clock=SystemClock(), enabled=True, buffer_max=10
    )
    event = build_product_event(
        event_id="e1",
        event_type="chat.turn",
        occurred_at=datetime.now(UTC),
        tenant_id="t",
    )
    await reporter.record(event)
    assert await reporter.flush_once() == 0
    assert await outbox.count_pending() == 1
    sink.fail = False
    assert await reporter.flush_once() == 1
    assert outbox.delivered_ids == ["e1"]


def test_event_sink_installed_reports() -> None:
    captured: list[ProductEvent] = []

    class _Q:
        def enqueue(self, event: ProductEvent) -> None:
            captured.append(event)

    install_event_sink(_Q())
    try:
        report_product_event("chat.turn", tenant_id="t", attributes={"status": "SUCCESS"})
    finally:
        install_event_sink(None)
    assert len(captured) == 1 and captured[0].event_type == "chat.turn"


# ── 控制面 HTTP 客户端 ──


async def test_control_client_etag_and_ack_payload() -> None:
    seen: list[httpx.Request] = []
    payload = _payload(3)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/control/snapshot":
            assert request.headers["If-None-Match"] == "v2"
            return httpx.Response(200, json=payload)
        if request.url.path == "/control/commands/claim":
            return httpx.Response(
                200,
                json={
                    "commands": [
                        {
                            "command_id": "c1",
                            "idempotency_key": "k1",
                            "action": "cache.clear",
                            "action_version": 1,
                            "params": {"tenant_key": "t", "datasource_key": "d"},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={})

    client = ControlPlaneClient(
        base_url="http://control.local",
        service_id="kongming",
        token_provider=StaticTokenProvider("tok"),
        transport=httpx.MockTransport(handler),
    )
    result = await client.fetch_snapshot(service_id="kongming", etag="v2")
    assert result.payload is not None and result.payload["version"] == 3
    commands = await client.claim(service_id="kongming", instance_id="i")
    assert commands[0].action == "cache.clear"
    await client.ack(
        service_id="kongming",
        command_id="c1",
        idempotency_key="k1",
        outcome=CommandOutcome(status="SUCCEEDED", result={"ok": True}),
    )
    assert all(r.headers["Authorization"] == "Bearer tok" for r in seen)


async def test_control_client_auth_failure_is_explicit() -> None:
    client = ControlPlaneClient(
        base_url="http://control.local",
        service_id="kongming",
        token_provider=StaticTokenProvider("tok"),
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={})),
    )
    from kongming.control.models import ControlAuthError

    with pytest.raises(ControlAuthError):
        await client.fetch_snapshot(service_id="kongming", etag="")


# ── 注册 ──


class _RegSink:
    def __init__(self) -> None:
        self.registered: list[dict[str, object]] = []
        self.heartbeats: list[dict[str, object]] = []

    async def register(self, **kwargs: object) -> None:
        self.registered.append(kwargs)

    async def heartbeat(self, **kwargs: object) -> None:
        self.heartbeats.append(kwargs)


class _RegStore:
    def __init__(self) -> None:
        self.attempts: list[dict[str, object]] = []

    async def record_attempt(self, **kwargs: object) -> None:
        self.attempts.append(kwargs)

    async def record_heartbeat(self, **kwargs: object) -> None:
        self.attempts.append(kwargs)


class _Health:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    async def check(self) -> HealthStatus:
        return HealthStatus(ready=self.ready, database=self.ready, redis=self.ready)


async def test_registration_reports_real_health_and_capabilities() -> None:
    sink, store = _RegSink(), _RegStore()
    svc = RegistrationService(
        sink=sink,
        store=store,
        health=_Health(ready=False),
        service_id="kongming",
        instance_id="i1",
        version="0.1.0",
    )
    assert await svc.register_once() is True
    call = sink.registered[0]
    assert call["version"] == "0.1.0"
    assert "chat" in cast(tuple[str, ...], call["capabilities"])
    assert cast(HealthStatus, call["health"]).ready is False  # 不伪造 ready
    assert await svc.heartbeat_once() is True


# ── 权益/目录 ──


async def test_entitlement_fail_closed_and_catalog_filter() -> None:
    store = _MemStore()
    snapshots = _snapshot_service(
        _FakeSource([_payload(1, _sections(bot_key="b1"))]), store, _Applier()
    )
    await snapshots.refresh()
    tenant_id = store.snapshot.sections.entitlements[0].tenant_id  # type: ignore[union-attr]
    ent = EntitlementService(
        snapshots=snapshots, mode="managed", default_limits=QuotaLimits(0, 0, 0, 0)
    )
    limits = await ent.for_tenant_id(tenant_id)
    assert limits.calls_daily == 10
    with pytest.raises(Exception, match="无有效权益"):
        await ent.for_tenant_id(str(uuid7()))
    catalog = CatalogService(snapshots=snapshots, entitlements=ent, mode="managed")
    entries = await catalog.entries_for_tenant("default")
    assert "b1" in entries


# ── HTTP 面:状态/本地命令/只读投影/安全闸 ──


async def test_system_control_status_and_local_command(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    await seed_all(session, get_settings())
    uid = await make_user("boss")
    rid = (
        await session.execute(text("SELECT id FROM public.sys_role WHERE role_key='SUPER_ADMIN'"))
    ).scalar_one()
    await session.execute(
        text("INSERT INTO public.sys_user_role (user_id, role_id) VALUES (:u, :r)"),
        {"u": uid, "r": rid},
    )
    await session.commit()
    token = _token(uid)
    r = await client.get("/system/control", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] in ("standalone", "managed")
    bad = await client.post(
        "/system/commands",
        json={"action": "shell.exec", "params": {"cmd": "x"}, "reason": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert bad.status_code == 200
    assert bad.json()["status"] == "REJECTED"
    rec = await client.get(
        "/system/commands/receipts", headers={"Authorization": f"Bearer {token}"}
    )
    assert rec.status_code == 200


def _token(uid: str) -> str:
    from kongming.auth import tokens

    return tokens.issue(uid, "access")


async def test_service_read_requires_identity(client: AsyncClient) -> None:
    runtime = get_control_runtime()
    verifier = runtime.reader_verifier
    if verifier.configured:
        pytest.skip("测试环境已配置读者身份,交由部署侧验收")
    r = await client.get("/service-read/summary")
    assert r.status_code in (401, 503)


async def test_service_read_projection_with_local_reader(
    mcp_client: AsyncClient, session: AsyncSession
) -> None:
    client = mcp_client
    from kongming.control.runtime import build_runtime

    await seed_all(session, get_settings())
    settings = Settings(
        control_mode="standalone",
        control_reader_token="reader-secret",
        control_readonly_enabled=True,
    )
    runtime = build_runtime(settings=settings, maker=get_sessionmaker(), redis=_redis())
    install_control_runtime(runtime)
    try:
        assert (await client.get("/service-read/summary")).status_code == 401
        assert (
            await client.get("/service-read/summary", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        ok = await client.get(
            "/service-read/summary", headers={"Authorization": "Bearer reader-secret"}
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["source"] == "kongming"
        assert "capabilities" in body
        assert "password" not in json.dumps(body).lower()
        assert (
            await client.get(
                "/service-read/nope", headers={"Authorization": "Bearer reader-secret"}
            )
        ).status_code == 404
        write = await client.post(
            "/service-read/summary", headers={"Authorization": "Bearer reader-secret"}
        )
        assert write.status_code in (404, 405), write.text  # 无写入口
    finally:
        await runtime.stop()
        install_control_runtime(None)


def _redis():  # type: ignore[no-untyped-def]
    from kongming.redis_client import get_redis

    return get_redis()


async def test_managed_security_gate(client: AsyncClient) -> None:
    from kongming.control.runtime import build_runtime

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/control/snapshot":
            return httpx.Response(200, json=_payload(1))
        return httpx.Response(200, json={})

    settings = Settings(
        control_mode="managed",
        control_base_url="http://control.local",
        control_signing_secret=SECRET,
        control_service_token="tok",
        control_instance_id="i",
    )
    runtime = build_runtime(
        settings=settings,
        maker=get_sessionmaker(),
        redis=_redis(),
        transport=httpx.MockTransport(handler),
    )
    install_control_runtime(runtime)
    try:
        blocked = await client.get("/chat/sessions")
        assert blocked.status_code == 503
        assert await runtime.snapshot.refresh() == "updated"
        now = await client.get("/chat/sessions")
        assert now.status_code == 401  # 安全上下文就绪,进入正常认证
        assert (await client.get("/healthz")).status_code == 200
    finally:
        await runtime.stop()
        install_control_runtime(None)


# ── 权益缺口 fail-closed(成本闸) ──


async def test_cost_gate_entitlement_missing_fails_closed() -> None:
    from kongming.chat.costgate import ChatCostGate, ChatQuotaUnavailable
    from kongming.control.models import EntitlementMissing

    class _Limiter:
        async def allow_sliding(self, key: str, *, limit: int, window_sec: int) -> bool:
            return True

    class _Counter:
        async def count_calls(self, **kwargs: object) -> tuple[int, int]:
            return 0, 0

    class _Quota:
        async def limits(self, tenant_id: str) -> object:
            raise EntitlementMissing("无权益")

    gate = ChatCostGate(
        limiter=cast(Any, _Limiter()),
        counter=cast(Any, _Counter()),
        rate_per_minute=0,
        quota_source=cast(Any, _Quota()),
    )
    with pytest.raises(ChatQuotaUnavailable):
        await gate.check_login(tenant_id="t", user_id="u")
