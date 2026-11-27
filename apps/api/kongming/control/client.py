"""控制面 HTTP 客户端:快照拉取/注册心跳/命令 claim-ack/用量推送。

wire 端点为 v1 形态,由 X-01 契约冻结;任何非 2xx 都转成域错误,
客户端不静默吞掉认证失败(401/403 必须显式暴露)。
"""

import json
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from kongming.control.capabilities import ADMIN_CAPABILITIES, CAPABILITIES, ENTITLEMENT_KEYS
from kongming.control.models import (
    CommandEnvelope,
    CommandOutcome,
    ControlAuthError,
    ControlUnavailable,
    FetchResult,
    HealthStatus,
    ProductEvent,
)
from kongming.control.ports import TokenProvider

_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_SNAPSHOT_PATH = "/control/snapshot"
_REGISTER_PATH = "/control/services/register"
_HEARTBEAT_PATH = "/control/services/heartbeat"
_CLAIM_PATH = "/control/commands/claim"
_ACK_PATH = "/control/commands/{command_id}/ack"
_USAGE_PATH = "/control/usage/events"


class ControlPlaneClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_id: str,
        token_provider: TokenProvider,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_sec: float = 5.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._service_id = service_id
        self._tokens = token_provider
        self._transport = transport
        self._timeout = timeout_sec

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            timeout=self._timeout,
            transport=self._transport,
            trust_env=False,
        )

    async def _headers(self) -> dict[str, str]:
        token = await self._tokens.token()
        return {
            "Authorization": f"Bearer {token}",
            "X-Kongming-Service": self._service_id,
            "Accept": "application/json",
        }

    @staticmethod
    def _raise_for_status(resp: httpx.Response, action: str) -> None:
        if resp.status_code in (401, 403):
            raise ControlAuthError(f"控制面拒绝{action}: {resp.status_code}")
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ControlUnavailable(f"控制面{action}失败({resp.status_code})")

    @staticmethod
    def _json(resp: httpx.Response) -> dict[str, Any]:
        if len(resp.content) > _MAX_RESPONSE_BYTES:
            raise ControlUnavailable("控制面响应超过大小上限")
        try:
            body = resp.json()
        except json.JSONDecodeError as e:
            raise ControlUnavailable("控制面响应不是合法 JSON") from e
        if not isinstance(body, dict):
            raise ControlUnavailable("控制面响应必须为对象")
        return cast("dict[str, Any]", body)

    async def fetch(self, *, service_id: str, etag: str) -> FetchResult:
        """SnapshotSource 端口形态(与 fetch_snapshot 同一实现)。"""
        return await self.fetch_snapshot(service_id=service_id, etag=etag)

    async def send(self, events: list[ProductEvent]) -> None:
        """UsageSink 端口形态(与 send_usage 同一实现)。"""
        await self.send_usage(events)

    async def fetch_snapshot(self, *, service_id: str, etag: str) -> FetchResult:
        headers = await self._headers()
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with self._http() as http:
                resp = await http.get(
                    _SNAPSHOT_PATH, params={"service_id": service_id}, headers=headers
                )
        except httpx.HTTPError as e:
            raise ControlUnavailable("控制快照拉取失败") from e
        if resp.status_code == 304:
            return FetchResult(not_modified=True, etag=etag)
        self._raise_for_status(resp, "快照拉取")
        return FetchResult(not_modified=False, etag=etag, payload=self._json(resp))

    async def register(
        self,
        *,
        service_id: str,
        instance_id: str,
        version: str,
        capabilities: tuple[str, ...] = CAPABILITIES,
        admin_capabilities: tuple[str, ...] = ADMIN_CAPABILITIES,
        entitlement_keys: tuple[str, ...] = ENTITLEMENT_KEYS,
        health: HealthStatus,
    ) -> None:
        payload: dict[str, object] = {
            "service_id": service_id,
            "instance_id": instance_id,
            "version": version,
            "capabilities": list(capabilities),
            "admin_capabilities": list(admin_capabilities),
            "entitlement_keys": list(entitlement_keys),
            "health": {
                "ready": health.ready,
                "database": health.database,
                "redis": health.redis,
            },
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        await self._post(_REGISTER_PATH, payload, "注册")

    async def heartbeat(
        self, *, service_id: str, instance_id: str, version: str, health: HealthStatus
    ) -> None:
        await self._post(
            _HEARTBEAT_PATH,
            {
                "service_id": service_id,
                "instance_id": instance_id,
                "version": version,
                "health": {
                    "ready": health.ready,
                    "database": health.database,
                    "redis": health.redis,
                },
                "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            "心跳",
        )

    async def claim(self, *, service_id: str, instance_id: str) -> list[CommandEnvelope]:
        body = await self._post(
            _CLAIM_PATH, {"service_id": service_id, "instance_id": instance_id}, "命令领取"
        )
        items = body.get("commands")
        if items is None:
            return []
        if not isinstance(items, list):
            raise ControlUnavailable("命令领取响应 commands 必须为数组")
        out: list[CommandEnvelope] = []
        for raw_item in cast("list[object]", items):
            if not isinstance(raw_item, dict):
                raise ControlUnavailable("命令条目必须为对象")
            raw = cast("dict[str, object]", raw_item)
            action_version = raw.get("action_version")
            out.append(
                CommandEnvelope(
                    command_id=str(raw.get("command_id") or ""),
                    idempotency_key=str(raw.get("idempotency_key") or ""),
                    action=str(raw.get("action") or ""),
                    action_version=action_version if isinstance(action_version, int) else 0,
                    params=cast("dict[str, object]", raw.get("params") or {}),
                    reason=str(raw.get("reason") or ""),
                    actor=str(raw.get("actor") or ""),
                )
            )
        return out

    async def ack(
        self,
        *,
        service_id: str,
        command_id: str,
        idempotency_key: str,
        outcome: CommandOutcome,
    ) -> None:
        await self._post(
            _ACK_PATH.format(command_id=command_id),
            {
                "service_id": service_id,
                "idempotency_key": idempotency_key,
                "status": outcome.status,
                "result": outcome.result,
                "error": outcome.error,
                "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            "命令回执",
        )

    async def send_usage(self, events: list[ProductEvent]) -> None:
        if not events:
            return
        await self._post(
            _USAGE_PATH,
            {
                "service_id": self._service_id,
                "events": [
                    {
                        "event_id": e.event_id,
                        "event_type": e.event_type,
                        "occurred_at": e.occurred_at.isoformat().replace("+00:00", "Z"),
                        "tenant_id": e.tenant_id,
                        "user_id": e.user_id,
                        "bot_key": e.bot_key,
                        "attributes": e.attributes,
                    }
                    for e in events
                ],
            },
            "用量上报",
        )

    async def _post(self, path: str, payload: dict[str, object], action: str) -> dict[str, Any]:
        headers = await self._headers()
        headers["Content-Type"] = "application/json"
        try:
            async with self._http() as http:
                resp = await http.post(path, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise ControlUnavailable(f"控制面{action}失败") from e
        self._raise_for_status(resp, action)
        if not resp.content:
            return {}
        return self._json(resp)
