"""快照载荷解析与验签(纯函数,零框架)。

X-01 wire 形态:`{"version","observed_at","service_id","etag","security_sections","sections"}`,
签名是响应头中的 RSA-SHA256 PKCS#1 v1.5 裸签名,覆盖原始响应体。
内部 last-known 仍按六节(tenants/members/...)持久化,解析后做一次投影。
任何字段类型不符/安全节缺失/条目超限都整体拒绝,不做部分接受。
"""

from datetime import UTC, datetime
from typing import cast

from cryptography.hazmat.primitives.asymmetric import rsa

from kongming.control.models import (
    SECURITY_SECTIONS,
    CatalogEntryRecord,
    CoarseRoleRecord,
    ControlSnapshot,
    EntitlementRecord,
    MembershipRecord,
    PolicyPackageRecord,
    SnapshotRejected,
    SnapshotSections,
    TenantRecord,
)
from kongming.control.verifier import verify_snapshot

MAX_ENTRIES_PER_SECTION = 50_000
MAX_RULE_NAMES_PER_PACKAGE = 500

# 锦官按服务投影时 kongming 的三个安全节。
KONGMING_SECURITY_SECTIONS = ("tenant_directory", "entitlements", "policy_packages")
# 权益配额维度 → Kongming 执行字段；未知维度不推断、不参与执行。
# 缺失/未知维度按未配置处理(执行字段 0=不限):套餐是跨服务维度集合，
# Muniu 维度不能成为 Kongming 的拒绝条件。
QUOTA_DIMENSIONS = {
    "calls_daily": "quota_calls_daily",
    "calls_monthly": "quota_calls_monthly",
    "bot_calls_daily": "quota_bot_calls_daily",
    "tool_calls_per_minute": "quota_tool_calls_per_minute",
}


def _require_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise SnapshotRejected(f"快照字段 {field} 必须为数组")
    items = cast("list[object]", value)
    if len(items) > MAX_ENTRIES_PER_SECTION:
        raise SnapshotRejected(f"快照节 {field} 条目超过上限 {MAX_ENTRIES_PER_SECTION}")
    return items


def _require_dict(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SnapshotRejected(f"快照字段 {field} 必须为对象")
    return cast("dict[str, object]", value)


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotRejected(f"快照字段 {field} 必须为非空字符串")
    return value.strip()


def _require_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotRejected(f"快照字段 {field} 必须为非负整数")
    return value


def _str_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SnapshotRejected(f"快照字段 {field} 必须为字符串数组")
    items = cast("list[object]", value)
    if not all(isinstance(v, str) for v in items):
        raise SnapshotRejected(f"快照字段 {field} 必须为字符串数组")
    return tuple(str(v) for v in items)


def _items(raw: dict[str, object], section: str) -> list[object]:
    block = raw.get(section)
    if not isinstance(block, dict):
        raise SnapshotRejected(f"快照缺安全节 {section}")
    items = cast("dict[str, object]", block).get("items")
    if not isinstance(items, list):
        raise SnapshotRejected(f"快照节 {section}.items 必须为数组")
    typed = cast("list[object]", items)
    if len(typed) > MAX_ENTRIES_PER_SECTION:
        raise SnapshotRejected(f"快照节 {section} 条目超过上限 {MAX_ENTRIES_PER_SECTION}")
    return typed


def _obj(value: object, section: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SnapshotRejected(f"快照节 {section} 条目必须为对象")
    return cast("dict[str, object]", value)


def _parse_tenants(raw: dict[str, object]) -> tuple[TenantRecord, ...]:
    out: list[TenantRecord] = []
    for item in _items(raw, "tenants"):
        o = _obj(item, "tenants")
        out.append(
            TenantRecord(
                tenant_id=_require_str(o.get("tenant_id"), "tenants.tenant_id"),
                tenant_key=_require_str(o.get("tenant_key"), "tenants.tenant_key"),
                tenant_name=_require_str(o.get("tenant_name"), "tenants.tenant_name"),
                status=str(o.get("status") or "ACTIVE"),
            )
        )
    return tuple(out)


def _parse_members(raw: dict[str, object]) -> tuple[MembershipRecord, ...]:
    out: list[MembershipRecord] = []
    for item in _items(raw, "members"):
        o = _obj(item, "members")
        email = o.get("email")
        out.append(
            MembershipRecord(
                user_id=_require_str(o.get("user_id"), "members.user_id"),
                username=_require_str(o.get("username"), "members.username"),
                nickname=str(o.get("nickname") or ""),
                email=str(email) if isinstance(email, str) and email else None,
                status=str(o.get("status") or "ACTIVE"),
                tenant_key=_require_str(o.get("tenant_key"), "members.tenant_key"),
                coarse_role_keys=_str_tuple(o.get("coarse_role_keys"), "members.coarse_role_keys"),
            )
        )
    return tuple(out)


def _parse_roles(raw: dict[str, object]) -> tuple[CoarseRoleRecord, ...]:
    out: list[CoarseRoleRecord] = []
    for item in _items(raw, "coarse_roles"):
        o = _obj(item, "coarse_roles")
        out.append(
            CoarseRoleRecord(
                role_key=_require_str(o.get("role_key"), "coarse_roles.role_key"),
                role_name=_require_str(o.get("role_name"), "coarse_roles.role_name"),
            )
        )
    return tuple(out)


def _parse_entitlements(raw: dict[str, object]) -> tuple[EntitlementRecord, ...]:
    out: list[EntitlementRecord] = []
    for item in _items(raw, "entitlements"):
        o = _obj(item, "entitlements")
        out.append(
            EntitlementRecord(
                tenant_id=_require_str(o.get("tenant_id"), "entitlements.tenant_id"),
                tenant_key=_require_str(o.get("tenant_key"), "entitlements.tenant_key"),
                status=str(o.get("status") or "ACTIVE"),
                features=_str_tuple(o.get("features"), "entitlements.features"),
                entitlement_keys=_str_tuple(
                    o.get("entitlement_keys"), "entitlements.entitlement_keys"
                ),
                quota_calls_daily=_require_int(
                    o.get("quota_calls_daily", 0), "entitlements.quota_calls_daily"
                ),
                quota_calls_monthly=_require_int(
                    o.get("quota_calls_monthly", 0), "entitlements.quota_calls_monthly"
                ),
                quota_bot_calls_daily=_require_int(
                    o.get("quota_bot_calls_daily", 0), "entitlements.quota_bot_calls_daily"
                ),
                quota_tool_calls_per_minute=_require_int(
                    o.get("quota_tool_calls_per_minute", 0),
                    "entitlements.quota_tool_calls_per_minute",
                ),
            )
        )
    return tuple(out)


def _parse_packages(raw: dict[str, object]) -> tuple[PolicyPackageRecord, ...]:
    out: list[PolicyPackageRecord] = []
    for item in _items(raw, "policy_packages"):
        o = _obj(item, "policy_packages")
        rules = _str_tuple(o.get("rule_names"), "policy_packages.rule_names")
        if len(rules) > MAX_RULE_NAMES_PER_PACKAGE:
            raise SnapshotRejected("policy_packages.rule_names 超过上限")
        status = str(o.get("status") or "ACTIVE")
        if status not in ("ACTIVE", "RETIRED"):
            raise SnapshotRejected(f"policy_packages.status 非法值 {status}")
        out.append(
            PolicyPackageRecord(
                package_key=_require_str(o.get("package_key"), "policy_packages.package_key"),
                version=_require_int(o.get("version", 1), "policy_packages.version"),
                status=status,
                rule_names=rules,
                tenant_keys=_str_tuple(o.get("tenant_keys"), "policy_packages.tenant_keys")
                or ("*",),
                tenant_ids=_str_tuple(o.get("tenant_ids"), "policy_packages.tenant_ids"),
            )
        )
    return tuple(out)


def _parse_catalog(raw: dict[str, object]) -> tuple[CatalogEntryRecord, ...]:
    block = raw.get("catalog")
    if block is None:
        return ()
    if not isinstance(block, dict):
        raise SnapshotRejected("快照节 catalog 必须为对象")
    items = cast("dict[str, object]", block).get("items")
    if not isinstance(items, list):
        raise SnapshotRejected("快照节 catalog.items 必须为数组")
    typed_items = cast("list[object]", items)
    if len(typed_items) > MAX_ENTRIES_PER_SECTION:
        raise SnapshotRejected("快照节 catalog 条目超过上限")
    out: list[CatalogEntryRecord] = []
    for item in typed_items:
        o = _obj(item, "catalog")
        listing = str(o.get("listing") or "LISTED")
        if listing not in ("LISTED", "UNLISTED"):
            raise SnapshotRejected(f"catalog.listing 非法值 {listing}")
        visibility = str(o.get("visibility") or "TENANTS")
        if visibility not in ("TENANTS", "ALL"):
            raise SnapshotRejected(f"catalog.visibility 非法值 {visibility}")
        entitlement_key = o.get("entitlement_key")
        out.append(
            CatalogEntryRecord(
                bot_key=_require_str(o.get("bot_key"), "catalog.bot_key"),
                listing=listing,
                visibility=visibility,
                tenant_keys=_str_tuple(o.get("tenant_keys"), "catalog.tenant_keys") or ("*",),
                entitlement_key=(
                    str(entitlement_key)
                    if isinstance(entitlement_key, str) and entitlement_key
                    else None
                ),
            )
        )
    return tuple(out)


def _parse_observed_at(value: object) -> datetime:
    text = _require_str(value, "observed_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as e:
        raise SnapshotRejected("快照 observed_at 非法") from e
    if parsed.tzinfo is None:
        raise SnapshotRejected("快照 observed_at 必须带时区")
    return parsed.astimezone(UTC)


def parse_sections(raw_sections: dict[str, object]) -> SnapshotSections:
    """把已验签的六节解析为类型化记录(也用于 last-known 读回)。"""
    missing = [name for name in SECURITY_SECTIONS if name not in raw_sections]
    if missing:
        raise SnapshotRejected(f"快照缺安全节 {missing}")
    return SnapshotSections(
        tenants=_parse_tenants(raw_sections),
        members=_parse_members(raw_sections),
        coarse_roles=_parse_roles(raw_sections),
        entitlements=_parse_entitlements(raw_sections),
        policy_packages=_parse_packages(raw_sections),
        catalog=_parse_catalog(raw_sections),
    )


def parse_snapshot(
    raw_body: bytes,
    payload: dict[str, object],
    *,
    expected_service_id: str,
    signature: str,
    public_key: rsa.RSAPublicKey,
) -> ControlSnapshot:
    """验签并解析 X-01 快照；任何不合规整体拒绝(SnapshotRejected)。"""
    verify_snapshot(public_key, raw_body, signature)
    service_id = _require_str(payload.get("service_id"), "service_id")
    if service_id != expected_service_id:
        raise SnapshotRejected(f"快照服务标识不符: {service_id}")
    version = _require_int(payload.get("version"), "version")
    if version < 1:
        raise SnapshotRejected("快照 version 必须 >= 1")
    observed_at_raw = _require_str(payload.get("observed_at"), "observed_at")
    etag = _require_str(payload.get("etag"), "etag")
    declared = _str_tuple(payload.get("security_sections"), "security_sections")
    missing_declared = [name for name in KONGMING_SECURITY_SECTIONS if name not in declared]
    if missing_declared:
        raise SnapshotRejected(f"快照缺安全节声明 {missing_declared}")
    raw_sections = _require_dict(payload.get("sections"), "sections")
    missing = [name for name in KONGMING_SECURITY_SECTIONS if name not in raw_sections]
    if missing:
        raise SnapshotRejected(f"快照缺安全节 {missing}")
    sections = _parse_jinguan_sections(raw_sections)
    return ControlSnapshot(
        service_id=service_id,
        version=version,
        observed_at=_parse_observed_at(observed_at_raw),
        sections=sections,
        etag=etag,
        signature=signature,
    )


def _parse_jinguan_sections(raw: dict[str, object]) -> SnapshotSections:
    """锦官分节 → Kongming 内部六节；成员/粗角色从租户目录派生。"""
    tenants, by_key = _j_tenants(_require_dict(raw.get("tenant_directory"), "tenant_directory"))
    entitlements = _j_entitlements(_require_dict(raw.get("entitlements"), "entitlements"), by_key)
    packages = _j_packages(_require_dict(raw.get("policy_packages"), "policy_packages"))
    members, roles = _j_members(
        _require_dict(raw.get("tenant_directory"), "tenant_directory"), by_key
    )
    catalog = _j_catalog(raw.get("market_catalog"))
    return SnapshotSections(
        tenants=tenants,
        members=members,
        coarse_roles=roles,
        entitlements=entitlements,
        policy_packages=packages,
        catalog=catalog,
    )


def _j_tenants(
    section: dict[str, object],
) -> tuple[tuple[TenantRecord, ...], dict[str, TenantRecord]]:
    tenants: list[TenantRecord] = []
    by_key: dict[str, TenantRecord] = {}
    for item in _require_list(section.get("tenants"), "tenant_directory.tenants"):
        o = _require_dict(item, "tenant_directory.tenants")
        tenant_id = _require_str(o.get("id"), "tenant_directory.tenants.id")
        tenant_key = _require_str(o.get("tenantKey"), "tenant_directory.tenants.tenantKey")
        name = str(o.get("name") or tenant_key).strip() or tenant_key
        value = str(o.get("status") or "ACTIVE").strip().upper()
        if value not in ("ACTIVE", "DISABLED"):
            raise SnapshotRejected(f"租户 {tenant_key} status 非法: {value}")
        tenant = TenantRecord(
            tenant_id=tenant_id,
            tenant_key=tenant_key,
            tenant_name=name,
            status=value,
        )
        tenants.append(tenant)
        by_key[tenant_key] = tenant
    return tuple(tenants), by_key


def _j_members(
    section: dict[str, object], by_key: dict[str, TenantRecord]
) -> tuple[tuple[MembershipRecord, ...], tuple[CoarseRoleRecord, ...]]:
    members: list[MembershipRecord] = []
    roles: dict[str, str] = {}
    for item in _require_list(section.get("tenants"), "tenant_directory.tenants"):
        o = _require_dict(item, "tenant_directory.tenants")
        tenant_key = _require_str(o.get("tenantKey"), "tenant_directory.tenants.tenantKey")
        if tenant_key not in by_key:
            raise SnapshotRejected(f"成员引用了不存在的租户键: {tenant_key}")
        for member in _require_list(o.get("members"), "tenant_directory.tenants.members"):
            m = _require_dict(member, "tenant_directory.tenants.members")
            subject = _require_str(m.get("subject"), "tenant_directory.tenants.members.subject")
            role = str(m.get("role") or "").strip()
            members.append(
                MembershipRecord(
                    user_id=subject,
                    username=subject,
                    nickname="",
                    email=None,
                    status=str(m.get("status") or "ACTIVE").strip().upper(),
                    tenant_key=tenant_key,
                    coarse_role_keys=(role,) if role else (),
                )
            )
            if role:
                roles.setdefault(role, role)
    return tuple(members), tuple(
        CoarseRoleRecord(role_key=k, role_name=v) for k, v in roles.items()
    )


def _j_entitlements(
    section: dict[str, object], by_key: dict[str, TenantRecord]
) -> tuple[EntitlementRecord, ...]:
    out: list[EntitlementRecord] = []
    for item in _require_list(section.get("tenants"), "entitlements.tenants"):
        o = _require_dict(item, "entitlements.tenants")
        tenant_key = _require_str(o.get("tenantKey"), "entitlements.tenants.tenantKey")
        tenant = by_key.get(tenant_key)
        if tenant is None:
            raise SnapshotRejected(f"权益引用了不存在的租户键: {tenant_key}")
        values = dict.fromkeys(QUOTA_DIMENSIONS.values(), 0)
        for quota in _require_list(o.get("quotas"), "entitlements.tenants.quotas"):
            q = _require_dict(quota, "entitlements.tenants.quotas")
            dimension = str(q.get("dimension") or "").strip().lower()
            field = QUOTA_DIMENSIONS.get(dimension)
            if field is None:
                continue
            limit = _require_int(q.get("limit", 0), "entitlements.tenants.quotas.limit")
            values[field] = limit
        out.append(
            EntitlementRecord(
                tenant_id=tenant.tenant_id,
                tenant_key=tenant_key,
                status=tenant.status,
                features=(),
                entitlement_keys=_str_tuple(
                    o.get("entitlementKeys"), "entitlements.tenants.entitlementKeys"
                ),
                quota_calls_daily=values["quota_calls_daily"],
                quota_calls_monthly=values["quota_calls_monthly"],
                quota_bot_calls_daily=values["quota_bot_calls_daily"],
                quota_tool_calls_per_minute=values["quota_tool_calls_per_minute"],
            )
        )
    return tuple(out)


def _j_packages(section: dict[str, object]) -> tuple[PolicyPackageRecord, ...]:
    out: list[PolicyPackageRecord] = []
    for item in _require_list(section.get("packages"), "policy_packages.packages"):
        o = _require_dict(item, "policy_packages.packages")
        service_id = _require_str(o.get("serviceId"), "policy_packages.packages.serviceId")
        if service_id != "kongming":
            raise SnapshotRejected(f"策略包投影服务不符: {service_id}")
        scope = _require_str(o.get("scope"), "policy_packages.packages.scope").upper()
        if scope == "GLOBAL":
            tenant_keys = ("*",)
        elif scope == "TENANT":
            tenant_keys = (_require_str(o.get("tenantKey"), "policy_packages.packages.tenantKey"),)
        else:
            raise SnapshotRejected(f"策略包 scope 非法: {scope}")
        out.append(
            PolicyPackageRecord(
                package_key=_require_str(
                    o.get("packageKey"), "policy_packages.packages.packageKey"
                ),
                version=1,
                status="ACTIVE",
                rule_names=(),
                tenant_keys=tenant_keys,
                tenant_ids=(),
            )
        )
    return tuple(out)


def _j_catalog(value: object) -> tuple[CatalogEntryRecord, ...]:
    if value is None:
        return ()
    section = _require_dict(value, "market_catalog")
    out: list[CatalogEntryRecord] = []
    for item in _require_list(section.get("entries"), "market_catalog.entries"):
        o = _require_dict(item, "market_catalog.entries")
        raw_visibility = _require_str(
            o.get("visibility"), "market_catalog.entries.visibility"
        ).upper()
        if raw_visibility == "PUBLIC":
            visibility = "ALL"
            tenant_keys = ("*",)
        elif raw_visibility == "TENANT":
            visibility = "TENANTS"
            tenant_keys = (_require_str(o.get("tenantKey"), "market_catalog.entries.tenantKey"),)
        else:
            raise SnapshotRejected(f"market_catalog.visibility 非法: {raw_visibility}")
        # 锦官目录以 enabled 表示上架；X-01 无 UNLISTED，缺省按 LISTED 消费。
        listing = str(o.get("listing") or "LISTED").strip().upper()
        if listing not in ("LISTED", "UNLISTED"):
            raise SnapshotRejected(f"market_catalog.listing 非法: {listing}")
        out.append(
            CatalogEntryRecord(
                bot_key=_require_str(o.get("botKey"), "market_catalog.entries.botKey"),
                listing=listing,
                visibility=visibility,
                tenant_keys=tenant_keys,
                entitlement_key=(
                    str(o["entitlementKey"])
                    if isinstance(o.get("entitlementKey"), str) and o.get("entitlementKey")
                    else None
                ),
            )
        )
    return tuple(out)


def serialize_sections(sections: SnapshotSections) -> dict[str, object]:
    """六节 → wire JSON(测试与落库共用,避免两套序列化漂移)。"""
    return {
        "tenants": {
            "items": [
                {
                    "tenant_id": t.tenant_id,
                    "tenant_key": t.tenant_key,
                    "tenant_name": t.tenant_name,
                    "status": t.status,
                }
                for t in sections.tenants
            ]
        },
        "members": {
            "items": [
                {
                    "user_id": m.user_id,
                    "username": m.username,
                    "nickname": m.nickname,
                    "email": m.email,
                    "status": m.status,
                    "tenant_key": m.tenant_key,
                    "coarse_role_keys": list(m.coarse_role_keys),
                }
                for m in sections.members
            ]
        },
        "coarse_roles": {
            "items": [
                {"role_key": r.role_key, "role_name": r.role_name} for r in sections.coarse_roles
            ]
        },
        "entitlements": {
            "items": [
                {
                    "tenant_id": e.tenant_id,
                    "tenant_key": e.tenant_key,
                    "status": e.status,
                    "features": list(e.features),
                    "entitlement_keys": list(e.entitlement_keys),
                    "quota_calls_daily": e.quota_calls_daily,
                    "quota_calls_monthly": e.quota_calls_monthly,
                    "quota_bot_calls_daily": e.quota_bot_calls_daily,
                    "quota_tool_calls_per_minute": e.quota_tool_calls_per_minute,
                }
                for e in sections.entitlements
            ]
        },
        "policy_packages": {
            "items": [
                {
                    "package_key": p.package_key,
                    "version": p.version,
                    "status": p.status,
                    "rule_names": list(p.rule_names),
                    "tenant_keys": list(p.tenant_keys),
                    "tenant_ids": list(p.tenant_ids),
                }
                for p in sections.policy_packages
            ]
        },
        "catalog": {
            "items": [
                {
                    "bot_key": c.bot_key,
                    "listing": c.listing,
                    "visibility": c.visibility,
                    "tenant_keys": list(c.tenant_keys),
                    "entitlement_key": c.entitlement_key,
                }
                for c in sections.catalog
            ]
        },
    }


def serialize_snapshot(snapshot: ControlSnapshot) -> dict[str, object]:
    """落库/测试构造用的规范化载荷。"""
    return {
        "service_id": snapshot.service_id,
        "version": snapshot.version,
        "observed_at": snapshot.observed_at.isoformat().replace("+00:00", "Z"),
        "etag": snapshot.etag,
        "sections": serialize_sections(snapshot.sections),
        "signature": snapshot.signature,
    }
