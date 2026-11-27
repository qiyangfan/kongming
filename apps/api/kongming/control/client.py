"""控制面 HTTP 客户端:快照拉取/注册心跳/命令 claim-ack/用量推送。

wire 端点为 v1 形态,由 X-01 契约冻结;任何非 2xx 都转成域错误,
客户端不静默吞掉认证失败(401/403 必须显式暴露)。
"""

import json
from datetime import UTC, datetime
from typing import cast

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
from kongming.control.ports import TenantKeyResolver, TokenProvider

_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_SNAPSHOT_PATH = "/control/v1/snapshot"
_REGISTER_PATH = "/control/v1/registry/register"
_HEARTBEAT_PATH = "/control/v1/registry/heartbeat"
_HEALTH_PATH = "/ingest/health"
_CLAIM_PATH = "/control/v1/commands/claim"
_ACK_PATH = "/control/v1/commands/{command_id}/ack"
_USAGE_PATH = "/ingest/events"


class ControlPlaneClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_id: str,
        environment: str,
        token_provider: TokenProvider,
        tenant_key_resolver: TenantKeyResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_sec: float = 5.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._service_id = service_id
        self._environment = environment or "local"
        self._tokens = token_provider
        self._tenant_keys = tenant_key_resolver
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
            "X-Service-Id": self._service_id,
            "Accept": "application/json",
        }

    @staticmethod
    def _raise_for_status(resp: httpx.Response, action: str) -> None:
        if resp.status_code in (401, 403):
            raise ControlAuthError(f"控制面拒绝{action}: {resp.status_code}")
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ControlUnavailable(f"控制面{action}失败({resp.status_code})")

    @staticmethod
    def _json(resp: httpx.Response) -> dict[str, object] | list[object]:
        if len(resp.content) > _MAX_RESPONSE_BYTES:
            raise ControlUnavailable("控制面响应超过大小上限")
        try:
            body: object = resp.json()
        except json.JSONDecodeError as e:
            raise ControlUnavailable("控制面响应不是合法 JSON") from e
        if isinstance(body, dict):
            return cast("dict[str, object]", body)
        if isinstance(body, list):
            return cast("list[object]", body)
        raise ControlUnavailable("控制面响应必须为对象或数组")

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
                resp = await http.get(_SNAPSHOT_PATH, headers=headers)
        except httpx.HTTPError as e:
            raise ControlUnavailable("控制快照拉取失败") from e
        if resp.status_code == 304:
            return FetchResult(not_modified=True, etag=etag)
        self._raise_for_status(resp, "快照拉取")
        payload = self._json(resp)
        if not isinstance(payload, dict):
            raise ControlUnavailable("控制快照响应必须为对象")
        return FetchResult(
            not_modified=False,
            etag=resp.headers.get("ETag", ""),
            payload=payload,
            raw=resp.content,
            signature=resp.headers.get("X-Jinguan-Signature", ""),
            key_id=resp.headers.get("X-Jinguan-Key-Id", ""),
        )

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
        declared = list(dict.fromkeys((*capabilities, *entitlement_keys)))
        payload: dict[str, object] = {
            "serviceId": service_id,
            "instanceId": instance_id,
            "environment": self._environment,
            "version": version,
            "capabilities": declared,
            "adminCapabilities": list(admin_capabilities),
        }
        await self._post(_REGISTER_PATH, payload, "注册")
        await self._report_health(service_id, instance_id, health)

    async def heartbeat(
        self, *, service_id: str, instance_id: str, version: str, health: HealthStatus
    ) -> None:
        await self._post(
            _HEARTBEAT_PATH,
            {"serviceId": service_id, "instanceId": instance_id},
            "心跳",
        )
        await self._report_health(service_id, instance_id, health)

    async def _report_health(self, service_id: str, instance_id: str, health: HealthStatus) -> None:
        """健康事件独立上报；失败不把注册/心跳判成故障。"""
        status = "UP" if health.ready else "DEGRADED"
        try:
            await self._post(
                _HEALTH_PATH,
                {
                    "serviceId": service_id,
                    "instanceId": instance_id,
                    "status": status,
                    "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
                "健康上报",
            )
        except Exception:  # noqa: BLE001  # 健康上报旁路失败不影响注册/心跳
            return

    async def claim(self, *, service_id: str, instance_id: str) -> list[CommandEnvelope]:
        body = await self._post(_CLAIM_PATH, {"limit": 10}, "命令领取")
        if not isinstance(body, list):
            raise ControlUnavailable("命令领取响应必须为数组")
        out: list[CommandEnvelope] = []
        for raw_item in body:
            if not isinstance(raw_item, dict):
                raise ControlUnavailable("命令条目必须为对象")
            raw = cast("dict[str, object]", raw_item)
            action_version = raw.get("actionSchemaVersion")
            command_id = str(raw.get("id") or "")
            out.append(
                CommandEnvelope(
                    command_id=command_id,
                    idempotency_key=command_id,
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
        status = "SUCCEEDED" if outcome.status == "SUCCEEDED" else "FAILED"
        payload: dict[str, object] = {"status": status}
        if status == "SUCCEEDED":
            payload["result"] = outcome.result
        else:
            payload["error"] = outcome.error or "命令执行失败"
        await self._post(
            _ACK_PATH.format(command_id=command_id),
            payload,
            "命令回执",
            allow_conflict=True,
        )

    async def send_usage(self, events: list[ProductEvent]) -> None:
        if not events:
            return
        items: list[dict[str, object]] = []
        for event in events:
            tenant_key = (
                await self._tenant_keys.key_for_id(event.tenant_id)
                if self._tenant_keys is not None
                else None
            )
            item: dict[str, object] = {
                "eventId": event.event_id,
                "metric": event.event_type,
                "quantity": 1,
                "unit": "event",
                "kind": "PRODUCT",
                "occurredAt": event.occurred_at.isoformat().replace("+00:00", "Z"),
            }
            if tenant_key:
                item["tenantKey"] = tenant_key
            if event.user_id:
                item["subject"] = event.user_id
            items.append(item)
        await self._post(_USAGE_PATH, {"events": items}, "用量上报")

    async def _post(
        self, path: str, payload: object, action: str, *, allow_conflict: bool = False
    ) -> dict[str, object] | list[object]:
        headers = await self._headers()
        headers["Content-Type"] = "application/json"
        try:
            async with self._http() as http:
                resp = await http.post(path, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise ControlUnavailable(f"控制面{action}失败") from e
        # 终态重复回执返回 409;本地已终态,按幂等成功处理
        if allow_conflict and resp.status_code == 409:
            return {}
        self._raise_for_status(resp, action)
        if not resp.content:
            return {}
        return self._json(resp)
