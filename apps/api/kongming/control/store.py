"""控制面持久化(SQLAlchemy):last-known 快照、命令回执、用量 outbox、注册状态。

只在基础设施层出现 SQL;域服务经 ports 使用。快照保存有版本单调守卫,
并发/迟到的旧版本无法覆盖新版本。
"""

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kongming.control.models import (
    CommandOutcome,
    ControlSnapshot,
    ProductEvent,
    SnapshotSections,
)
from kongming.control.payload import parse_sections, serialize_sections


class SqlSnapshotStore:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def load(self, *, service_id: str) -> ControlSnapshot | None:
        async with self._maker() as s:
            row = (
                (
                    await s.execute(
                        text(
                            "SELECT version, etag, observed_at, sections, signature"
                            " FROM control.snapshot WHERE service_id = :sid"
                        ),
                        {"sid": service_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        sections_raw = row["sections"]
        if isinstance(sections_raw, str):
            sections_raw = json.loads(sections_raw)
        if not isinstance(sections_raw, dict):
            raise TypeError("控制快照 sections 损坏")
        sections: SnapshotSections = parse_sections(cast("dict[str, object]", sections_raw))
        return ControlSnapshot(
            service_id=service_id,
            version=int(row["version"]),
            observed_at=cast("datetime", row["observed_at"]),
            sections=sections,
            etag=str(row["etag"] or ""),
            signature=str(row["signature"] or ""),
        )

    async def save(self, snapshot: ControlSnapshot) -> bool:
        payload = _sections_json(snapshot.sections)
        async with self._maker() as s:
            result = await s.execute(
                text(
                    "INSERT INTO control.snapshot"
                    " (service_id, version, etag, observed_at, fetched_at, sections, signature)"
                    " VALUES (:sid, :ver, :etag, :obs, :now, CAST(:sec AS jsonb), :sig)"
                    " ON CONFLICT (service_id) DO UPDATE SET"
                    "  version = EXCLUDED.version, etag = EXCLUDED.etag,"
                    "  observed_at = EXCLUDED.observed_at, fetched_at = EXCLUDED.fetched_at,"
                    "  sections = EXCLUDED.sections, signature = EXCLUDED.signature"
                    " WHERE control.snapshot.version < EXCLUDED.version"
                ),
                {
                    "sid": snapshot.service_id,
                    "ver": snapshot.version,
                    "etag": snapshot.etag,
                    "obs": snapshot.observed_at,
                    "now": datetime.now(UTC),
                    "sec": payload,
                    "sig": snapshot.signature,
                },
            )
            await s.commit()
        return bool(cast("Any", result).rowcount)


class SqlCommandReceiptStore:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def get(self, *, idempotency_key: str) -> CommandOutcome | None:
        async with self._maker() as s:
            row = (
                (
                    await s.execute(
                        text(
                            "SELECT status, result FROM control.command_receipt"
                            " WHERE idempotency_key = :key"
                        ),
                        {"key": idempotency_key},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["status"] == "EXECUTING":
            return None
        result = row["result"]
        if isinstance(result, str):
            result = json.loads(result)
        return CommandOutcome(
            status=row["status"],
            result=cast("dict[str, object]", result or {}),
        )

    async def begin(
        self,
        *,
        idempotency_key: str,
        command_id: str,
        action: str,
        action_version: int,
        source: str,
        actor: str,
    ) -> bool:
        async with self._maker() as s:
            result = await s.execute(
                text(
                    "INSERT INTO control.command_receipt"
                    " (idempotency_key, command_id, action, action_version, source, status,"
                    "  result, actor, created_at, updated_at)"
                    " VALUES (:key, :cid, :action, :ver, :src, 'EXECUTING',"
                    "  CAST('{}' AS jsonb), :actor, now(), now())"
                    " ON CONFLICT (idempotency_key) DO NOTHING"
                ),
                {
                    "key": idempotency_key,
                    "cid": command_id,
                    "action": action,
                    "ver": action_version,
                    "src": source,
                    "actor": actor,
                },
            )
            await s.commit()
        return bool(cast("Any", result).rowcount)

    async def finish(
        self,
        *,
        idempotency_key: str,
        status: str,
        result: dict[str, object],
    ) -> None:
        async with self._maker() as s:
            await s.execute(
                text(
                    "UPDATE control.command_receipt"
                    " SET status = :status, result = CAST(:res AS jsonb), updated_at = now()"
                    " WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key, "status": status, "res": json.dumps(result)},
            )
            await s.commit()

    async def recent(self, *, limit: int) -> list[dict[str, object]]:
        async with self._maker() as s:
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT idempotency_key, command_id, action, action_version, source,"
                            " status, result, actor, updated_at"
                            " FROM control.command_receipt ORDER BY updated_at DESC LIMIT :limit"
                        ),
                        {"limit": max(1, min(limit, 200))},
                    )
                )
                .mappings()
                .all()
            )
        out: list[dict[str, object]] = []
        for r in rows:
            result = r["result"]
            if isinstance(result, str):
                result = json.loads(result)
            out.append(
                {
                    "idempotency_key": r["idempotency_key"],
                    "command_id": r["command_id"],
                    "action": r["action"],
                    "action_version": r["action_version"],
                    "source": r["source"],
                    "status": r["status"],
                    "result": result,
                    "actor": r["actor"],
                    "updated_at": r["updated_at"].isoformat(),
                }
            )
        return out


class SqlUsageOutbox:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def add(self, event: ProductEvent) -> None:
        async with self._maker() as s:
            await s.execute(
                text(
                    "INSERT INTO control.usage_outbox"
                    " (event_id, event_type, occurred_at, payload, attempts,"
                    "  next_attempt_at, created_at)"
                    " VALUES (:id, :type, :at, CAST(:payload AS jsonb), 0, now(), now())"
                    " ON CONFLICT (event_id) DO NOTHING"
                ),
                {
                    "id": event.event_id,
                    "type": event.event_type,
                    "at": event.occurred_at,
                    "payload": json.dumps(_event_payload(event), ensure_ascii=False),
                },
            )
            await s.commit()

    async def pending(self, *, limit: int) -> list[ProductEvent]:
        async with self._maker() as s:
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT payload FROM control.usage_outbox"
                            " WHERE delivered_at IS NULL AND next_attempt_at <= now()"
                            " ORDER BY occurred_at ASC LIMIT :limit"
                        ),
                        {"limit": limit},
                    )
                )
                .mappings()
                .all()
            )
        out: list[ProductEvent] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                out.append(_event_from_payload(cast("dict[str, object]", payload)))
        return out

    async def delivered(self, *, event_ids: list[str]) -> None:
        if not event_ids:
            return
        async with self._maker() as s:
            await s.execute(
                text(
                    "UPDATE control.usage_outbox SET delivered_at = now()"
                    " WHERE event_id = ANY(:ids)"
                ),
                {"ids": event_ids},
            )
            await s.commit()

    async def failed(self, *, event_ids: list[str], next_attempt_at: datetime) -> None:
        if not event_ids:
            return
        async with self._maker() as s:
            await s.execute(
                text(
                    "UPDATE control.usage_outbox"
                    " SET attempts = attempts + 1, next_attempt_at = :next"
                    " WHERE event_id = ANY(:ids)"
                ),
                {"ids": event_ids, "next": next_attempt_at},
            )
            await s.commit()

    async def count_pending(self) -> int:
        async with self._maker() as s:
            return int(
                (
                    await s.execute(
                        text("SELECT count(*) FROM control.usage_outbox WHERE delivered_at IS NULL")
                    )
                ).scalar_one()
            )

    async def trim_to(self, *, limit: int) -> int:
        async with self._maker() as s:
            result = await s.execute(
                text(
                    "DELETE FROM control.usage_outbox WHERE event_id IN ("
                    " SELECT event_id FROM control.usage_outbox WHERE delivered_at IS NULL"
                    " ORDER BY occurred_at ASC OFFSET :keep)"
                ),
                {"keep": max(limit, 0)},
            )
            await s.commit()
        return int(cast("Any", result).rowcount or 0)


class SqlRegistrationStore:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def record_attempt(
        self,
        *,
        service_id: str,
        instance_id: str,
        version: str,
        capabilities: tuple[str, ...],
        error: str | None,
        registered: bool,
    ) -> None:
        async with self._maker() as s:
            await s.execute(
                text(
                    "INSERT INTO control.registration"
                    " (service_id, instance_id, version, capabilities, last_attempt_at,"
                    "  last_registered_at, last_error)"
                    " VALUES (:sid, :iid, :ver, CAST(:caps AS jsonb), now(),"
                    "  CASE WHEN :ok THEN now() ELSE NULL END, :err)"
                    " ON CONFLICT (service_id, instance_id) DO UPDATE SET"
                    "  version = EXCLUDED.version, capabilities = EXCLUDED.capabilities,"
                    "  last_attempt_at = now(),"
                    "  last_registered_at = CASE WHEN :ok THEN now()"
                    "    ELSE control.registration.last_registered_at END,"
                    "  last_error = :err"
                ),
                {
                    "sid": service_id,
                    "iid": instance_id,
                    "ver": version,
                    "caps": json.dumps(list(capabilities)),
                    "ok": registered,
                    "err": error,
                },
            )
            await s.commit()

    async def record_heartbeat(
        self, *, service_id: str, instance_id: str, error: str | None
    ) -> None:
        async with self._maker() as s:
            await s.execute(
                text(
                    "UPDATE control.registration SET last_heartbeat_at ="
                    " CASE WHEN :ok THEN now() ELSE last_heartbeat_at END,"
                    " last_error = :err"
                    " WHERE service_id = :sid AND instance_id = :iid"
                ),
                {"sid": service_id, "iid": instance_id, "ok": error is None, "err": error},
            )
            await s.commit()


def _sections_json(sections: SnapshotSections) -> str:
    """落库只存六节;服务标识/版本/签名分列存放。"""
    return json.dumps(serialize_sections(sections), ensure_ascii=False)


def _event_payload(event: ProductEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "tenant_id": event.tenant_id,
        "user_id": event.user_id,
        "bot_key": event.bot_key,
        "attributes": event.attributes,
    }


def _event_from_payload(payload: dict[str, object]) -> ProductEvent:
    occurred = payload.get("occurred_at")
    attributes = payload.get("attributes")
    return ProductEvent(
        event_id=str(payload.get("event_id") or ""),
        event_type=str(payload.get("event_type") or ""),
        occurred_at=datetime.fromisoformat(str(occurred)) if occurred else datetime.now(UTC),
        tenant_id=str(payload.get("tenant_id") or ""),
        user_id=str(payload["user_id"]) if payload.get("user_id") else None,
        bot_key=str(payload["bot_key"]) if payload.get("bot_key") else None,
        attributes=cast("dict[str, object]", attributes or {}),
    )
