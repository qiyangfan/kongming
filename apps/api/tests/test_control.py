# pyright: reportPrivateUsage=false
"""控制面消费者回归:快照验签/版本/安全闸、命令、用量、注册、只读投影。

外部控制面一律用 httpx.MockTransport;不发起真实网络。
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from httpx import AsyncClient
from jwt.algorithms import RSAAlgorithm
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
    CommandEnvelope,
    CommandOutcome,
    ControlSnapshot,
    EntitlementMissing,
    EntitlementRecord,
    FetchResult,
    HealthStatus,
    MembershipRecord,
    PolicyPackageRecord,
    ProductEvent,
    SnapshotRejected,
    SnapshotSections,
    TenantRecord,
)
from kongming.control.payload import parse_snapshot
from kongming.control.registration import RegistrationService
from kongming.control.snapshot import SnapshotService, SystemClock
from kongming.control.snapshot_keys import JwksSnapshotKeySource, StaticSnapshotKeySource
from kongming.control.store import SqlCommandReceiptStore
from kongming.control.usage import ProductEventRejected, UsageReporter, build_product_event
from kongming.db import get_sessionmaker, uuid7
from tests.conftest import MakeUser

_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = _SIGNING_KEY.public_key()
PUBLIC_PEM = PUBLIC_KEY.public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


def _sign_raw(raw: bytes) -> str:
    signature = _SIGNING_KEY.sign(raw, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def _signed(payload: dict[str, object]) -> tuple[bytes, str]:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return raw, _sign_raw(raw)


def _public_jwk(key: rsa.RSAPublicKey, kid: str = "kid-1") -> dict[str, object]:
    jwk = RSAAlgorithm.to_jwk(key, as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


def _sections(
    *,
    tenant_key: str = "default",
    bot_key: str | None = None,
    listing: str = "LISTED",
    with_entitlement: bool = True,
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
                listing=listing,
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


def _wire_sections(sections: SnapshotSections) -> dict[str, object]:
    """内部六节 → 锦官 X-01 分节，供联调测试构造。"""
    entitlements_by_key = {e.tenant_key: e for e in sections.entitlements}
    tenants: list[dict[str, object]] = []
    for tenant in sections.tenants:
        members: list[dict[str, object]] = [
            {
                "subject": m.user_id,
                "role": m.coarse_role_keys[0] if m.coarse_role_keys else "MEMBER",
                "status": m.status,
            }
            for m in sections.members
            if m.tenant_key == tenant.tenant_key
        ]
        tenants.append(
            {
                "id": tenant.tenant_id,
                "tenantKey": tenant.tenant_key,
                "name": tenant.tenant_name,
                "status": tenant.status,
                "members": members,
            }
        )
    entitlement_rows: list[dict[str, object]] = []
    for key, item in entitlements_by_key.items():
        quotas: list[dict[str, object]] = []
        for dimension, field in (
            ("calls_daily", "quota_calls_daily"),
            ("calls_monthly", "quota_calls_monthly"),
            ("bot_calls_daily", "quota_bot_calls_daily"),
            ("tool_calls_per_minute", "quota_tool_calls_per_minute"),
        ):
            value = getattr(item, field)
            if value:
                quotas.append({"dimension": dimension, "unit": "count", "limit": value})
        entitlement_rows.append(
            {
                "tenantKey": key,
                "planKey": "pro",
                "boundAt": datetime.now(UTC).isoformat(),
                "quotas": quotas,
                "entitlementKeys": list(item.entitlement_keys),
            }
        )
    package_rows: list[dict[str, object]] = [
        {
            "packageKey": p.package_key,
            "serviceId": "kongming",
            "scope": "GLOBAL" if "*" in p.tenant_keys else "TENANT",
            "tenantKey": None if "*" in p.tenant_keys else p.tenant_keys[0],
            "rulesetVersion": str(p.version),
            "updatedAt": datetime.now(UTC).isoformat(),
        }
        for p in sections.policy_packages
    ]
    catalog_rows: list[dict[str, object]] = [
        {
            "entryKey": c.bot_key,
            "botKey": c.bot_key,
            "visibility": "PUBLIC" if c.visibility == "ALL" else "TENANT",
            "tenantKey": None if "*" in c.tenant_keys else c.tenant_keys[0],
            "entitlementKey": c.entitlement_key,
            "featured": False,
            "listing": c.listing,
        }
        for c in sections.catalog
    ]
    return {
        "tenant_directory": {"tenantCount": len(tenants), "tenants": tenants},
        "entitlements": {"tenantCount": len(entitlement_rows), "tenants": entitlement_rows},
        "policy_packages": {"packageCount": len(package_rows), "packages": package_rows},
        "market_catalog": {"entryCount": len(catalog_rows), "entries": catalog_rows},
    }


def _payload(
    version: int, sections: SnapshotSections | None = None, **over: object
) -> dict[str, object]:
    data: dict[str, object] = {
        "service_id": "kongming",
        "version": version,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "etag": f"v{version}",
        "security_sections": ["tenant_directory", "entitlements", "policy_packages"],
        "sections": _wire_sections(sections or _sections()),
    }
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
        raw, signature = _signed(item)
        return FetchResult(
            not_modified=False,
            etag=str(item["etag"]),
            payload=item,
            raw=raw,
            signature=signature,
            key_id="test-kid",
        )


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

    async def apply(self, sections: SnapshotSections, *, service_id: str, version: int) -> None:
        self.applied.append(version)


def _snapshot_service(source: _FakeSource, store: _MemStore, applier: _Applier) -> SnapshotService:
    return SnapshotService(
        source=source,
        store=store,
        applier=applier,
        clock=SystemClock(),
        service_id="kongming",
        keys=StaticSnapshotKeySource(PUBLIC_KEY),
        stale_after_sec=300,
        enabled=True,
    )


# ── 快照:验签/版本/安全闸 ──


def test_snapshot_signature_and_security_sections() -> None:
    payload = _payload(1)
    raw, signature = _signed(payload)
    snap = parse_snapshot(
        raw, payload, expected_service_id="kongming", signature=signature, public_key=PUBLIC_KEY
    )
    assert snap.version == 1
    tampered = raw.replace(b'"v1"', b'"drift"', 1)
    with pytest.raises(SnapshotRejected, match="签名"):
        parse_snapshot(
            tampered,
            {**payload, "etag": "drift"},
            expected_service_id="kongming",
            signature=signature,
            public_key=PUBLIC_KEY,
        )
    missing = cast(dict[str, object], payload["sections"]).copy()
    missing.pop("entitlements")
    missing_payload = {**payload, "sections": missing}
    missing_raw, missing_sig = _signed(missing_payload)
    with pytest.raises(SnapshotRejected, match="安全节"):
        parse_snapshot(
            missing_raw,
            missing_payload,
            expected_service_id="kongming",
            signature=missing_sig,
            public_key=PUBLIC_KEY,
        )
    blank_etag = {**payload, "etag": ""}
    blank_raw, blank_sig = _signed(blank_etag)
    with pytest.raises(SnapshotRejected, match="etag"):
        parse_snapshot(
            blank_raw,
            blank_etag,
            expected_service_id="kongming",
            signature=blank_sig,
            public_key=PUBLIC_KEY,
        )


async def test_jwks_key_source_refreshes_for_new_kid() -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.url.path == "/.well-known/jwks.json"
        calls += 1
        keys = [RSAAlgorithm.to_jwk(PUBLIC_KEY, as_dict=True)]
        keys[0]["kid"] = "kid-1"
        if calls > 1:
            second = RSAAlgorithm.to_jwk(other_key.public_key(), as_dict=True)
            second["kid"] = "kid-2"
            keys.append(second)
        return httpx.Response(200, json={"keys": keys})

    source = JwksSnapshotKeySource(
        url="http://control.local/.well-known/jwks.json",
        transport=httpx.MockTransport(handler),
        force_refresh_min_sec=0,
    )
    assert await source.key("kid-1") is not None
    assert calls == 1
    assert await source.key("kid-2") is not None
    assert calls == 2


def test_jinguan_catalog_without_listing_maps_to_listed() -> None:
    payload = _payload(1, _sections(bot_key="assistant"))
    sections = cast("dict[str, object]", payload["sections"])
    catalog_block = cast("dict[str, object]", sections["market_catalog"])
    entries = cast("list[dict[str, object]]", catalog_block["entries"])
    entries[0].pop("listing")
    raw, signature = _signed(payload)
    snap = parse_snapshot(
        raw, payload, expected_service_id="kongming", signature=signature, public_key=PUBLIC_KEY
    )
    assert snap.sections.catalog[0].listing == "LISTED"


def test_snapshot_rejects_tenant_scope_without_tenant_key() -> None:
    payload = _payload(1, _sections(bot_key="assistant"))
    sections = cast("dict[str, object]", payload["sections"])
    packages_block = cast("dict[str, object]", sections["policy_packages"])
    packages = cast("list[dict[str, object]]", packages_block["packages"])
    packages.append(
        {
            "packageKey": "tenant-only",
            "serviceId": "kongming",
            "scope": "TENANT",
            "tenantKey": None,
            "rulesetVersion": "v1",
            "updatedAt": datetime.now(UTC).isoformat(),
        }
    )
    raw, signature = _signed(payload)
    with pytest.raises(SnapshotRejected, match="tenantKey"):
        parse_snapshot(
            raw, payload, expected_service_id="kongming", signature=signature, public_key=PUBLIC_KEY
        )

    payload = _payload(1, _sections(bot_key="assistant"))
    sections = cast("dict[str, object]", payload["sections"])
    catalog_block = cast("dict[str, object]", sections["market_catalog"])
    entries = cast("list[dict[str, object]]", catalog_block["entries"])
    entries[0]["tenantKey"] = None
    raw, signature = _signed(payload)
    with pytest.raises(SnapshotRejected, match="tenantKey"):
        parse_snapshot(
            raw, payload, expected_service_id="kongming", signature=signature, public_key=PUBLIC_KEY
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
    assert applier.applied == [2, 2]  # 同版本重拉会幂等重试身份补齐


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
    await SqlIdentityApplier(get_sessionmaker()).apply(sections, service_id="kongming", version=1)
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
        SnapshotSections(tenants=sections.tenants, members=(), coarse_roles=(), entitlements=()),
        service_id="kongming",
        version=2,
    )
    left = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_user_tenant WHERE user_id = CAST(:u AS uuid)"),
            {"u": uid},
        )
    ).scalar_one()
    assert int(left) == 0


async def test_identity_cache_preserves_local_profile(
    session: AsyncSession, make_user: MakeUser
) -> None:
    from kongming.control.identity_cache import SqlIdentityApplier

    uid = await make_user("alice", email="alice@example.com", nickname="Alice")
    sections = SnapshotSections(
        tenants=(TenantRecord(tenant_id=str(uuid7()), tenant_key="t1", tenant_name="租户一"),),
        members=(
            MembershipRecord(
                user_id=uid,
                username=uid,
                nickname="",
                email=None,
                status="ACTIVE",
                tenant_key="t1",
            ),
        ),
    )
    await SqlIdentityApplier(get_sessionmaker()).apply(sections, service_id="kongming", version=1)
    row = (
        await session.execute(
            text(
                "SELECT username, email, nickname, status FROM public.sys_user"
                " WHERE id = CAST(:u AS uuid)"
            ),
            {"u": uid},
        )
    ).one()
    assert tuple(row) == ("alice", "alice@example.com", "Alice", "ACTIVE")


async def test_identity_cache_disabled_membership_is_tenant_scoped(
    session: AsyncSession, make_user: MakeUser
) -> None:
    from kongming.control.identity_cache import SqlIdentityApplier

    uid = await make_user("bob", email="bob@example.com", nickname="Bob")
    sections = SnapshotSections(
        tenants=(TenantRecord(tenant_id=str(uuid7()), tenant_key="t1", tenant_name="租户一"),),
        members=(
            MembershipRecord(
                user_id=uid,
                username=uid,
                nickname="",
                email=None,
                status="DISABLED",
                tenant_key="t1",
            ),
        ),
    )
    await SqlIdentityApplier(get_sessionmaker()).apply(sections, service_id="kongming", version=1)
    user = (
        await session.execute(
            text("SELECT status FROM public.sys_user WHERE id = CAST(:u AS uuid)"),
            {"u": uid},
        )
    ).scalar_one()
    memberships = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_user_tenant WHERE user_id = CAST(:u AS uuid)"),
            {"u": uid},
        )
    ).scalar_one()
    assert user == "ACTIVE" and int(memberships) == 0


async def test_identity_apply_version_is_monotonic(session: AsyncSession) -> None:
    from kongming.control.identity_cache import SqlIdentityApplier

    applier = SqlIdentityApplier(get_sessionmaker())
    newer = SnapshotSections(
        tenants=(TenantRecord(tenant_id=str(uuid7()), tenant_key="v2", tenant_name="二"),),
        entitlements=(),
    )
    older = SnapshotSections(
        tenants=(TenantRecord(tenant_id=str(uuid7()), tenant_key="v1", tenant_name="一"),),
        entitlements=(),
    )
    await applier.apply(newer, service_id="kongming", version=2)
    await applier.apply(older, service_id="kongming", version=1)
    keys = (
        (await session.execute(text("SELECT tenant_key FROM public.sys_tenant ORDER BY 1")))
        .scalars()
        .all()
    )
    assert keys == ["v2"]
    version = (
        await session.execute(
            text("SELECT version FROM control.identity_state WHERE service_id='kongming'")
        )
    ).scalar_one()
    assert int(version) == 2


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


async def test_command_rejects_non_object_params() -> None:
    calls: list[dict[str, object]] = []

    async def ok(params: dict[str, object], *, actor: str) -> dict[str, object]:
        calls.append(params)
        return {"ok": True}

    executor = CommandExecutor(
        receipts=await _receipts(),
        handlers={"cache.clear": ok},
        audit=cast(Any, _NullAudit()),
    )
    outcome = await executor.process(
        CommandEnvelope(
            command_id="c",
            idempotency_key="bad-params",
            action="cache.clear",
            action_version=1,
            params=cast(Any, []),
            actor="a",
        )
    )
    assert outcome.status == "REJECTED"
    assert not calls


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


async def test_command_audit_includes_reason() -> None:
    entries: list[dict[str, object]] = []

    class _Audit:
        async def log(
            self,
            *,
            actor: str | None,
            action: str,
            target: str,
            detail: dict[str, object],
        ) -> None:
            entries.append(detail)

    async def ok(params: dict[str, object], *, actor: str) -> dict[str, object]:
        return {"ok": True}

    executor = CommandExecutor(
        receipts=await _receipts(),
        handlers={"cache.clear": ok},
        audit=cast(Any, _Audit()),
    )
    await executor.execute(
        command_id="c-reason",
        idempotency_key="k-reason",
        action="cache.clear",
        action_version=1,
        params={"tenant_key": "t", "datasource_key": "d"},
        actor="a",
        source="local",
        reason="排障清理",
    )
    assert entries[0]["reason"] == "排障清理"


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


async def test_usage_outbox_trim_keeps_newest(session: AsyncSession) -> None:
    from kongming.control.store import SqlUsageOutbox

    outbox = SqlUsageOutbox(get_sessionmaker())
    base = datetime.now(UTC) - timedelta(minutes=10)
    ids = [uuid7().hex for _ in range(4)]
    for index, event_id in enumerate(ids):
        await outbox.add(
            ProductEvent(
                event_id=event_id,
                event_type="chat.turn",
                occurred_at=base + timedelta(seconds=index),
                tenant_id="t",
            )
        )
    assert await outbox.trim_to(limit=2) == 2
    pending = await outbox.pending(limit=10)
    assert [e.event_id for e in pending] == ids[2:]


async def test_usage_outbox_cap_enforced_on_flush() -> None:
    class _TightOutbox(_MemOutbox):
        def __init__(self) -> None:
            super().__init__()
            self.trims: list[int] = []

        async def trim_to(self, *, limit: int) -> int:
            self.trims.append(limit)
            keep = self.items[-limit:] if limit > 0 else []
            dropped = len(self.items) - len(keep)
            self.items = keep
            return dropped

    outbox = _TightOutbox()
    reporter = UsageReporter(
        outbox=cast(Any, outbox),
        sink=_Sink(fail=True),
        clock=SystemClock(),
        enabled=True,
        buffer_max=3,
    )
    for index in range(5):
        outbox.items.append(
            ProductEvent(
                event_id=f"cap-{index}",
                event_type="chat.turn",
                occurred_at=datetime.now(UTC) - timedelta(seconds=10 - index),
                tenant_id="t",
            )
        )
    assert await reporter.flush_once() == 0
    assert outbox.trims == [3]
    assert await outbox.count_pending() <= 3


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
    raw, signature = _signed(payload)
    ack_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/control/v1/snapshot":
            assert request.headers["If-None-Match"] == "v2"
            return httpx.Response(
                200,
                content=raw,
                headers={
                    "ETag": "v3",
                    "X-Jinguan-Signature": signature,
                    "X-Jinguan-Key-Id": "kid-1",
                },
            )
        if request.url.path == "/control/v1/commands/claim":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "c1",
                        "serviceId": "kongming",
                        "action": "cache.clear",
                        "actionSchemaVersion": 1,
                        "params": {"tenant_key": "t", "datasource_key": "d"},
                        "reason": "test",
                        "actor": "ops",
                        "status": "CLAIMED",
                        "attempts": 1,
                        "maxAttempts": 3,
                        "createdAt": "2026-11-27T00:00:00Z",
                        "updatedAt": "2026-11-27T00:00:00Z",
                    }
                ],
            )
        if request.url.path == "/control/v1/commands/c1/ack":
            ack_body.update(cast("dict[str, object]", json.loads(request.content)))
            return httpx.Response(204)
        return httpx.Response(200, json={})

    client = ControlPlaneClient(
        base_url="http://control.local",
        service_id="kongming",
        environment="local",
        token_provider=StaticTokenProvider("tok"),
        transport=httpx.MockTransport(handler),
    )
    result = await client.fetch_snapshot(service_id="kongming", etag="v2")
    assert result.payload is not None and result.payload["version"] == 3
    assert result.etag == "v3" and result.signature == signature
    assert result.raw == raw and result.key_id == "kid-1"
    commands = await client.claim(service_id="kongming", instance_id="i")
    assert commands[0].action == "cache.clear"
    assert commands[0].idempotency_key == "c1"
    await client.ack(
        service_id="kongming",
        command_id="c1",
        idempotency_key="k1",
        outcome=CommandOutcome(status="SUCCEEDED", result={"ok": True}),
    )
    assert ack_body == {"status": "SUCCEEDED", "result": {"ok": True}}
    assert all(r.headers["Authorization"] == "Bearer tok" for r in seen)
    assert all(r.headers["X-Service-Id"] == "kongming" for r in seen)


async def test_control_client_ack_treats_terminal_conflict_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/control/v1/commands/c1/ack"
        return httpx.Response(409, json={"detail": "already terminal"})

    client = ControlPlaneClient(
        base_url="http://control.local",
        service_id="kongming",
        environment="local",
        token_provider=StaticTokenProvider("tok"),
        transport=httpx.MockTransport(handler),
    )
    await client.ack(
        service_id="kongming",
        command_id="c1",
        idempotency_key="c1",
        outcome=CommandOutcome(status="SUCCEEDED", result={"ok": True}),
    )


async def test_control_client_registers_entitlement_keys_as_capabilities() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(cast("dict[str, object]", json.loads(request.content)))
        return httpx.Response(200, json={})

    client = ControlPlaneClient(
        base_url="http://control.local",
        service_id="kongming",
        environment="local",
        token_provider=StaticTokenProvider("tok"),
        transport=httpx.MockTransport(handler),
    )
    await client.register(
        service_id="kongming",
        instance_id="i",
        version="0.1.0",
        capabilities=("chat",),
        admin_capabilities=("role.manage",),
        entitlement_keys=("chat.calls", "bot.runs"),
        health=HealthStatus(ready=True, database=True, redis=True),
    )
    assert payloads[0] == {
        "serviceId": "kongming",
        "instanceId": "i",
        "environment": "local",
        "version": "0.1.0",
        "capabilities": ["chat", "chat.calls", "bot.runs"],
        "adminCapabilities": ["role.manage"],
    }
    assert payloads[1]["status"] == "UP"


async def test_control_client_auth_failure_is_explicit() -> None:
    client = ControlPlaneClient(
        base_url="http://control.local",
        service_id="kongming",
        environment="local",
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


class _FlakyRegSink(_RegSink):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    async def heartbeat(self, **kwargs: object) -> None:
        if self.fail:
            raise ConnectionError("down")
        await super().heartbeat(**kwargs)


async def test_registration_audits_heartbeat_transitions_only() -> None:
    sink, store = _FlakyRegSink(), _RegStore()
    actions: list[str] = []

    class _Audit:
        async def log(
            self,
            *,
            actor: str | None,
            action: str,
            target: str,
            detail: dict[str, object],
        ) -> None:
            actions.append(action)

    svc = RegistrationService(
        sink=sink,
        store=store,
        health=_Health(ready=True),
        service_id="kongming",
        instance_id="i1",
        version="0.1.0",
        audit=cast(Any, _Audit()),
    )
    assert await svc.register_once() is True
    assert await svc.heartbeat_once() is True  # 注册成功后的首次心跳不重复审计
    sink.fail = True
    assert await svc.heartbeat_once() is False
    assert await svc.heartbeat_once() is False  # 连续失败不重复审计
    sink.fail = False
    assert await svc.heartbeat_once() is True
    assert actions == [
        "control.registration",
        "control.heartbeat_failed",
        "control.heartbeat_recovered",
    ]


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
    ent = EntitlementService(
        snapshots=snapshots, mode="managed", default_limits=QuotaLimits(0, 0, 0, 0)
    )
    limits = await ent.for_tenant_key("default")
    assert limits.calls_daily == 10
    with pytest.raises(Exception, match="无有效权益"):
        await ent.for_tenant_key("missing")
    catalog = CatalogService(snapshots=snapshots, entitlements=ent, mode="managed")
    entries = await catalog.entries_for_tenant("default")
    assert "b1" in entries


async def test_catalog_unlisted_is_direct_only() -> None:
    store = _MemStore()
    snapshots = _snapshot_service(
        _FakeSource([_payload(1, _sections(bot_key="u1", listing="UNLISTED"))]),
        store,
        _Applier(),
    )
    await snapshots.refresh()
    ent = EntitlementService(
        snapshots=snapshots, mode="managed", default_limits=QuotaLimits(0, 0, 0, 0)
    )
    catalog = CatalogService(snapshots=snapshots, entitlements=ent, mode="managed")
    assert await catalog.entries_for_tenant("default") == {}
    assert await catalog.allows_entry("default", "u1") is True
    assert await catalog.allows_entry("default", "other") is False


async def test_entitlement_matches_tenant_key_not_snapshot_id(session: AsyncSession) -> None:
    from kongming.control.identity_cache import SqlTenantKeyResolver

    await seed_all(session, get_settings())
    local_id = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    store = _MemStore()
    snapshots = _snapshot_service(_FakeSource([_payload(1)]), store, _Applier())
    await snapshots.refresh()
    ent = EntitlementService(
        snapshots=snapshots,
        mode="managed",
        default_limits=QuotaLimits(0, 0, 0, 0),
        resolver=SqlTenantKeyResolver(get_sessionmaker()),
    )
    limits = await ent.for_tenant_id(str(local_id))
    assert limits.calls_daily == 10


async def test_policy_scope_uses_tenant_key() -> None:
    from kongming.control.policy import PolicyService

    class _Resolver:
        async def key_for_id(self, tenant_id: str) -> str | None:
            return "default"

    def _snap(package_key: str, tenant_keys: tuple[str, ...]) -> SnapshotService:
        sections = SnapshotSections(
            tenants=(),
            entitlements=(),
            policy_packages=(
                PolicyPackageRecord(
                    package_key=package_key,
                    version=1,
                    status="ACTIVE",
                    rule_names=("injection",),
                    tenant_keys=tenant_keys,
                ),
            ),
        )
        return _snapshot_service(_FakeSource([_payload(1, sections)]), _MemStore(), _Applier())

    other = _snap("other-pkg", ("other",))
    scoped = await other.refresh()
    assert scoped == "updated"
    policy = PolicyService(
        snapshots=other,
        mode="managed",
        local_rule_names=frozenset({"injection"}),
        resolver=cast(Any, _Resolver()),
    )
    assert await policy.rule_names_for_tenant("local-id") == frozenset()

    default = _snap("default-pkg", ("default",))
    await default.refresh()
    policy = PolicyService(
        snapshots=default,
        mode="managed",
        local_rule_names=frozenset({"injection"}),
        resolver=cast(Any, _Resolver()),
    )
    assert await policy.rule_names_for_tenant("local-id") == frozenset({"injection"})


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
        projection = await client.get(
            "/service/v1/read-projection", headers={"Authorization": "Bearer reader-secret"}
        )
        assert projection.status_code == 200, projection.text
        envelope = projection.json()
        assert envelope["service_id"] == "kongming"
        assert envelope["sections"]["summary"]["source"] == "kongming"
        assert (await client.get("/service/v1/read-projection")).status_code == 401
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


def test_standalone_domain_uses_bot_daily_quota_setting() -> None:
    from kongming.control.runtime import build_runtime

    runtime = build_runtime(
        settings=Settings(bot_quota_daily_calls=7, chat_quota_daily_calls=500),
        maker=get_sessionmaker(),
        redis=_redis(),
    )
    defaults = cast(Any, runtime.entitlements)._defaults
    assert defaults.bot_calls_daily == 7
    assert defaults.calls_daily == 500


async def test_control_cleanup_dry_run_and_prune(session: AsyncSession) -> None:
    from kongming.control.runtime import build_runtime
    from kongming.control.store import SqlUsageOutbox

    old = datetime.now(UTC) - timedelta(days=2)
    outbox = SqlUsageOutbox(get_sessionmaker())
    await outbox.add(
        ProductEvent(
            event_id="cleanup-event",
            event_type="chat.turn",
            occurred_at=old,
            tenant_id="t",
        )
    )
    await session.execute(
        text("UPDATE control.usage_outbox SET delivered_at=:old WHERE event_id='cleanup-event'"),
        {"old": old},
    )
    for key, status in (("cleanup-done", "SUCCEEDED"), ("cleanup-running", "EXECUTING")):
        await session.execute(
            text(
                "INSERT INTO control.command_receipt"
                " (idempotency_key, command_id, action, action_version, source, status,"
                "  result, actor, created_at, updated_at)"
                " VALUES (:k, :k, 'cache.clear', 1, 'control', :s, '{}', '', :old, :old)"
            ),
            {"k": key, "s": status, "old": old},
        )
    await session.execute(
        text(
            "INSERT INTO control.registration"
            " (service_id, instance_id, version, capabilities, last_attempt_at,"
            "  last_registered_at)"
            " VALUES ('kongming', 'old-instance', '0.1.0', '[]', :old, :old)"
        ),
        {"old": old},
    )
    await session.commit()

    runtime = build_runtime(
        settings=Settings(
            control_usage_retention_days=1,
            control_receipt_retention_days=1,
            control_registration_retention_days=1,
        ),
        maker=get_sessionmaker(),
        redis=_redis(),
    )
    dry = await runtime.cleanup_once(dry_run=True)
    assert dry == {"usage_delivered": 1, "command_receipts": 1, "registrations": 1}
    remaining = (
        await session.execute(text("SELECT count(*) FROM control.usage_outbox"))
    ).scalar_one()
    assert int(remaining) == 1

    pruned = await runtime.cleanup_once()
    assert pruned == {"usage_delivered": 1, "command_receipts": 1, "registrations": 1}
    assert (
        await session.execute(text("SELECT count(*) FROM control.usage_outbox"))
    ).scalar_one() == 0
    assert (
        await session.execute(
            text("SELECT count(*) FROM control.command_receipt WHERE status='EXECUTING'")
        )
    ).scalar_one() == 1
    assert (
        await session.execute(text("SELECT count(*) FROM control.registration"))
    ).scalar_one() == 0
    audits = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='control.cleanup'")
        )
    ).scalar_one()
    assert int(audits) == 2


def test_managed_runtime_generates_unique_instance_ids() -> None:
    from kongming.control.runtime import build_runtime

    base: dict[str, Any] = {
        "control_mode": "managed",
        "control_base_url": "http://control.local",
        "control_snapshot_public_key_pem": PUBLIC_PEM,
        "control_service_token": "tok",
    }
    first = build_runtime(settings=Settings(**base), maker=get_sessionmaker(), redis=_redis())
    second = build_runtime(settings=Settings(**base), maker=get_sessionmaker(), redis=_redis())
    first_registration = cast(Any, first._registration)
    second_registration = cast(Any, second._registration)
    first_id = first_registration._instance_id
    second_id = second_registration._instance_id
    assert first_id and second_id
    assert first_id != second_id
    assert first_id != "kongming"
    assert cast(Any, first)._instance_id == first_id
    explicit = build_runtime(
        settings=Settings(control_instance_id="fixed-instance", **base),
        maker=get_sessionmaker(),
        redis=_redis(),
    )
    assert cast(Any, explicit)._instance_id == "fixed-instance"
    from kongming.guardrail.policy_scope import install_policy_scope

    install_policy_scope(None)


async def test_managed_security_gate(client: AsyncClient) -> None:
    from kongming.control.runtime import build_runtime

    payload = _payload(1)
    raw, signature = _signed(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [_public_jwk(PUBLIC_KEY)]})
        if request.url.path == "/control/v1/snapshot":
            return httpx.Response(
                200,
                content=raw,
                headers={
                    "ETag": "v1",
                    "X-Jinguan-Signature": signature,
                    "X-Jinguan-Key-Id": "kid-1",
                },
            )
        return httpx.Response(200, json={})

    settings = Settings(
        control_mode="managed",
        control_base_url="http://control.local",
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


async def test_store_catalog_missing_entitlement_maps_503(session: AsyncSession) -> None:
    from fastapi import HTTPException

    from kongming.control.dependencies import install_control_runtime
    from kongming.store.router import _filter_catalog, _require_catalog

    class _Catalog:
        managed = True

        async def entries_for_tenant(self, tenant_key: str) -> dict[str, object]:
            raise EntitlementMissing(f"无权益:{tenant_key}")

        async def allows_entry(self, tenant_key: str, bot_key: str) -> bool:
            raise EntitlementMissing(f"无权益:{tenant_key}")

    class _Runtime:
        catalog = _Catalog()

    await seed_all(session, get_settings())
    tenant_id = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    install_control_runtime(cast(Any, _Runtime()))
    try:
        with pytest.raises(HTTPException) as filtered:
            await _filter_catalog(session, [], str(tenant_id))
        assert filtered.value.status_code == 503
        with pytest.raises(HTTPException) as required:
            await _require_catalog(session, user_id="", tenant_id=str(tenant_id), bot_key="bot")
        assert required.value.status_code == 503
    finally:
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
