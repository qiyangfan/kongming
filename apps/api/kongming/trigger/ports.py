"""trigger 端口(031):存储与目标执行反转,用例/调度零框架依赖。"""

from typing import Any, Protocol


class TriggerRepo(Protocol):
    async def create(self, **kw: Any) -> str: ...

    async def update(self, **kw: Any) -> bool: ...

    async def get(self, *, tenant_id: str, tg_key: str) -> dict[str, Any] | None: ...

    async def get_by_id(self, *, trigger_id: str) -> dict[str, Any] | None: ...

    async def list_all(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def set_enabled(self, *, trigger_id: str, tenant_id: str, enabled: bool) -> bool: ...

    async def delete(self, *, trigger_id: str, tenant_id: str) -> bool: ...

    async def find_by_webhook_path(self, *, path: str) -> dict[str, Any] | None: ...

    async def list_due_cron(self, *, now_iso: str) -> list[dict[str, Any]]: ...

    async def mark_fired(
        self, *, trigger_id: str, last_fired_iso: str, next_fire_iso: str | None
    ) -> None: ...

    async def insert_run(
        self,
        *,
        tenant_id: str,
        trigger_id: str,
        status: str,
        error: str,
        duration_ms: int,
        target_run_ref: str,
    ) -> str: ...

    async def page_runs(
        self, *, tenant_id: str, trigger_id: str, page: int, size: int
    ) -> tuple[int, list[dict[str, Any]]]: ...

    async def commit(self) -> None: ...


class TargetRunner(Protocol):
    """目标执行(031):实现侧装配 029/025 组合根(独立会话);
    返回 (success, ref, error)。"""

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
    ) -> tuple[bool, str, str]: ...


class TargetChecker(Protocol):
    """目标存在性(040):创建/改指时写边界 fail-fast。

    workflow=创建者视角可见集合(与前端候选同源,与派发期 032 可见性同口径);
    bot=租户级存在(与派发期 get_by_key 同口径)。实现见 router 组合根。
    """

    async def target_exists(
        self, *, target_type: str, target_key: str, tenant_id: str, creator_id: str
    ) -> bool: ...


class SecretCipher(Protocol):
    """密钥加解密(008 cipher 反转)。"""

    def encrypt(self, plain: str) -> str: ...

    def decrypt(self, encoded: str) -> str: ...


class Notifier(Protocol):
    """出站通知(031):实现侧 httpx+SSRF 判定(029 拒绝段复用);
    check_url 在 post 前由 service 调用。"""

    async def check_url(self, url: str) -> None: ...

    async def post(self, *, url: str, body: bytes, signature: str) -> None: ...
