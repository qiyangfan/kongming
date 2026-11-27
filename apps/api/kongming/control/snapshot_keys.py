"""快照签名公钥来源：锦官 JWKS 按 kid 选择并缓存，静态 PEM 仅作离线 pin。"""

import asyncio
import logging
import time
from typing import cast

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from kongming.control.models import ControlUnavailable

log = logging.getLogger(__name__)

_DEFAULT_TTL_SEC = 300.0
_FORCE_REFRESH_MIN_SEC = 30.0


class StaticSnapshotKeySource:
    """显式 pin 的单公钥；kid 仍必须存在，但由调用方保证与 key 匹配。"""

    def __init__(self, key: rsa.RSAPublicKey) -> None:
        self._key = key

    async def key(self, key_id: str) -> rsa.RSAPublicKey | None:
        return self._key if key_id.strip() else None


class JwksSnapshotKeySource:
    """从锦官 JWKS 拉取 RSA 公钥；未知 kid 触发限速强制刷新。"""

    def __init__(
        self,
        *,
        url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_sec: float = 10.0,
        ttl_sec: float = _DEFAULT_TTL_SEC,
        force_refresh_min_sec: float = _FORCE_REFRESH_MIN_SEC,
    ) -> None:
        self._url = url
        self._transport = transport
        self._timeout = timeout_sec
        self._ttl = ttl_sec
        self._force_min = force_refresh_min_sec
        self._keys: dict[str, rsa.RSAPublicKey] | None = None
        self._loaded_at = 0.0
        self._forced_at = 0.0
        self._lock = asyncio.Lock()

    async def key(self, key_id: str) -> rsa.RSAPublicKey | None:
        if not key_id.strip():
            return None
        keys = await self._load(force=False)
        found = keys.get(key_id)
        if found is not None:
            return found
        keys = await self._load(force=True)
        return keys.get(key_id)

    async def _load(self, *, force: bool) -> dict[str, rsa.RSAPublicKey]:
        async with self._lock:
            now = time.monotonic()
            if self._keys is not None and not force and now - self._loaded_at < self._ttl:
                return self._keys
            if self._keys is not None and force and now - self._forced_at < self._force_min:
                return self._keys
            if force:
                self._forced_at = now
            try:
                fetched = await self._fetch()
            except ControlUnavailable:
                if self._keys is not None:
                    log.warning("JWKS 刷新失败，继续使用缓存公钥", exc_info=True)
                    return self._keys
                raise
            self._keys = fetched
            self._loaded_at = now
            return fetched

    async def _fetch(self) -> dict[str, rsa.RSAPublicKey]:
        return _parse_rsa_keys(await self._get_json())

    async def _get_json(self) -> list[object]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                trust_env=False,
            ) as http:
                resp = await http.get(self._url, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise ControlUnavailable("锦官 JWKS 拉取失败") from e
        if resp.status_code != 200:
            raise ControlUnavailable(f"锦官 JWKS 返回 {resp.status_code}")
        try:
            body: object = resp.json()
        except ValueError as e:
            raise ControlUnavailable("锦官 JWKS 不是合法 JSON") from e
        if not isinstance(body, dict):
            raise ControlUnavailable("锦官 JWKS 必须为对象")
        raw_keys = cast("dict[str, object]", body).get("keys")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise ControlUnavailable("锦官 JWKS 为空")
        return cast("list[object]", raw_keys)


def _parse_rsa_keys(raw_keys: list[object]) -> dict[str, rsa.RSAPublicKey]:
    """解析 JWKS 中的 RSA 公钥；畸形条目整体拒绝。"""
    keys: dict[str, rsa.RSAPublicKey] = {}
    for item in raw_keys:
        if not isinstance(item, dict):
            raise ControlUnavailable("锦官 JWKS 条目非法")
        entry = cast("dict[str, object]", item)
        if entry.get("kty") != "RSA":
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str) or not kid.strip():
            continue
        try:
            key = RSAAlgorithm.from_jwk(entry)
        except (TypeError, ValueError) as e:
            raise ControlUnavailable("锦官 JWKS 公钥非法") from e
        if not isinstance(key, rsa.RSAPublicKey):
            raise ControlUnavailable("锦官 JWKS 必须是 RSA 公钥")
        keys[kid] = key
    if not keys:
        raise ControlUnavailable("锦官 JWKS 没有可用的 RSA 公钥")
    return keys
