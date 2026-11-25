"""voice 域持久化与外存端口,以 Protocol 隔离用例层与具体实现(ADR 惯例)。"""

from typing import Protocol

from kongming.voice.models import Ticket


class SignalingGateway(Protocol):
    """牵线:代浏览器与语音模型服务交换实时连接信息,原样返回应答。"""

    async def exchange(self, offer: bytes) -> bytes:
        """失败抛 SignalingFailed(原文不透传,日志不含正文)。"""
        ...


class AdmissionGate(Protocol):
    """准入闸:同一用户至多一通 + 部署级并发上限;故障 fail-closed 由实现保证。"""

    async def try_acquire_user(self, user_id: str) -> bool: ...
    async def release_user(self, user_id: str) -> None: ...
    async def try_acquire_slot(self) -> bool: ...
    async def release_slot(self) -> None: ...


class TicketStore(Protocol):
    """通道票据:一次性、短时效(票据本身即通道凭据;归属经通话核对)。"""

    async def issue(self, user_id: str, call_id: str, ttl_sec: int) -> Ticket: ...
    async def consume(self, token: str) -> Ticket | None:
        """取出即删(原子一次性);缺失/复用返回 None。"""
        ...


class CallGuard(Protocol):
    """建连频次与 24 小时预算闸;故障由实现 fail-open(准入仍 fail-closed)。"""

    async def allow_establish(self, user_id: str) -> bool:
        """单位时间建联次数是否放行(不占预算)。"""
        ...

    async def reserve_user_day(self, user_id: str) -> bool:
        """占用该用户 24 小时预算;失败返回 False。"""
        ...

    async def reserve_deployment_day(self) -> bool:
        """占用部署级 24 小时预算;失败返回 False。"""
        ...

    async def release_user_day(self, user_id: str) -> None:
        """释放一次用户预算(建立失败回滚)。"""
        ...

    async def release_deployment_day(self) -> None:
        """释放一次部署预算(建立失败回滚)。"""
        ...


class OperLogSink(Protocol):
    """操作审计出口(复用既有操作审计存储,不建新表)。"""

    async def add(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...
