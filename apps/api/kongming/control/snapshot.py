"""快照用例:拉取 → 验签/版本单调 → 落 last-known → 应用身份缓存。

不 import HTTP/SQL;所有外设在 ports。安全类的 fail-closed 判定也在此:
managed 且无任何有效快照时,受保护操作一律拒绝(由运行时层接该判定)。
"""

import logging
from datetime import UTC, datetime, timedelta

from kongming.control.models import (
    ControlSnapshot,
    ControlUnavailable,
    FetchResult,
    SecurityContextMissing,
    SnapshotRejected,
    SnapshotSections,
    SnapshotView,
)
from kongming.control.payload import parse_snapshot
from kongming.control.ports import Clock, SnapshotApplier, SnapshotSource, SnapshotStore

log = logging.getLogger(__name__)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SnapshotService:
    def __init__(
        self,
        *,
        source: SnapshotSource,
        store: SnapshotStore,
        applier: SnapshotApplier | None,
        clock: Clock,
        service_id: str,
        signing_secret: str,
        stale_after_sec: int,
        enabled: bool,
    ) -> None:
        self._source = source
        self._store = store
        self._applier = applier
        self._clock = clock
        self._service_id = service_id
        self._secret = signing_secret
        self._stale_after = stale_after_sec
        self._enabled = enabled
        self._current: ControlSnapshot | None = None
        self._loaded = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _ensure_loaded(self) -> ControlSnapshot | None:
        if self._loaded:
            return self._current
        self._loaded = True
        try:
            self._current = await self._store.load(service_id=self._service_id)
        except Exception:
            log.exception("last-known 快照读取失败")
            self._current = None
        return self._current

    async def current(self) -> ControlSnapshot | None:
        return await self._ensure_loaded()

    async def refresh(self) -> str:
        """拉取一次。返回 updated|not_modified|unavailable|rejected。"""
        if not self._enabled:
            raise ControlUnavailable("控制面未启用")
        current = await self._ensure_loaded()
        etag = current.etag if current is not None else ""
        result = await self._fetch(etag)
        if result is None:
            return "unavailable"
        if result.not_modified:
            if current is not None and not await self._apply_identity(current):
                return "unavailable"
            return "not_modified"
        return await self._apply(result.payload, current)

    async def _fetch(self, etag: str) -> FetchResult | None:
        try:
            return await self._source.fetch(service_id=self._service_id, etag=etag)
        except Exception as e:  # noqa: BLE001
            log.warning("控制快照拉取失败(保留 last-known): %s", e)
            return None

    def _guard(self, snapshot: ControlSnapshot, current: ControlSnapshot | None) -> str | None:
        """版本单调判定;返回拒绝原因或 None。"""
        if current is None:
            return None
        if snapshot.version < current.version:
            return f"版本回退 {current.version} -> {snapshot.version}"
        if snapshot.version == current.version and snapshot.etag != current.etag:
            return f"同版本 ETag 漂移 {snapshot.version}"
        return None

    async def _apply(
        self, payload: dict[str, object] | None, current: ControlSnapshot | None
    ) -> str:
        if payload is None:
            log.warning("控制快照响应缺载荷,保留 last-known")
            return "rejected"
        try:
            snapshot = parse_snapshot(
                payload, expected_service_id=self._service_id, secret=self._secret
            )
        except SnapshotRejected:
            log.warning("控制快照被拒绝(验签/格式/安全节)", exc_info=True)
            return "rejected"
        reason = self._guard(snapshot, current)
        if reason is not None:
            log.warning("控制快照被拒绝: %s", reason)
            return "rejected"
        if current is not None and snapshot.version == current.version:
            return await self._reconcile_identity(snapshot, success="not_modified")
        persisted = await self._persist(snapshot)
        if persisted != "updated":
            return persisted
        return await self._reconcile_identity(snapshot, success="updated")

    async def _reconcile_identity(self, snapshot: ControlSnapshot, *, success: str) -> str:
        if not await self._apply_identity(snapshot):
            return "unavailable"
        return success

    async def _persist(self, snapshot: ControlSnapshot) -> str:
        try:
            stored = await self._store.save(snapshot)
        except Exception:
            log.exception("控制快照落库失败")
            return "unavailable"
        if not stored:
            log.warning("控制快照落库被版本单调守卫拒绝: %s", snapshot.version)
            return "rejected"
        self._current = snapshot
        log.info("控制快照更新: version=%s %s", snapshot.version, snapshot.sections.digest())
        return "updated"

    async def _apply_identity(self, snapshot: ControlSnapshot) -> bool:
        if self._applier is None:
            return True
        try:
            await self._applier.apply(
                snapshot.sections, service_id=snapshot.service_id, version=snapshot.version
            )
        except Exception:
            # 快照已落库时身份必须按版本补齐;304 与同版本路径都会重试应用
            log.exception("控制快照身份缓存应用失败,保留 last-known 并等待重试")
            return False
        return True

    async def view(self) -> SnapshotView:
        current = await self._ensure_loaded()
        if current is None:
            return SnapshotView(present=False)
        now = self._clock.now()
        stale = now - current.observed_at > timedelta(seconds=self._stale_after)
        return SnapshotView(
            present=True,
            version=current.version,
            observed_at=current.observed_at,
            stale=stale,
            sections=current.sections,
        )

    async def require_security(self) -> SnapshotSections:
        """受保护操作的统一安全上下文;无有效快照即 fail-closed。"""
        current = await self._ensure_loaded()
        if current is None:
            raise SecurityContextMissing("无有效安全类快照,拒绝受保护操作")
        return current.sections
