"""voice 域基础设施:Redis 准入与票据、httpx 牵线(D9/D10)。

准入对 Redis 故障 fail-closed 由本层保证(读不到即拒绝);牵线对上游错误
统一收敛为 SignalingFailed 且日志不含请求与应答正文。
"""

import json
import logging
import secrets

import httpx
from redis.asyncio import Redis

from kongming.config import Settings
from kongming.voice.errors import SignalingFailed
from kongming.voice.models import Ticket
from kongming.voice.ports import AdmissionGate, TicketStore

log = logging.getLogger(__name__)

_USER_KEY = "voice:call:user:"
_SLOT_KEY = "voice:call:slots"
_TICKET_KEY = "voice:ticket:"

# 牵线超时与应答上限(D10):15 秒;失败不自动重试
SIGNALING_TIMEOUT_SEC = 15.0


class RedisAdmissionGate(AdmissionGate):
    """Redis 准入闸:同用户 SET NX EX;并发槽位原子计数带兜底过期。"""

    _ACQUIRE_SLOT_LUA = """
local n = redis.call('INCR', KEYS[1])
if n > tonumber(ARGV[1]) then
  redis.call('DECR', KEYS[1])
  return 0
end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""
    _RELEASE_SLOT_LUA = """
local n = redis.call('GET', KEYS[1])
if n and tonumber(n) > 0 then
  redis.call('DECR', KEYS[1])
end
return 1
"""

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._r = redis
        self._max_concurrent = settings.voice_max_concurrent
        # 占位过期=时长上限+余量:进程异常退出后占位自动消失
        self._ttl = settings.voice_max_call_seconds + 120

    async def try_acquire_user(self, user_id: str) -> bool:
        return bool(await self._r.set(_USER_KEY + user_id, "1", ex=self._ttl, nx=True))

    async def release_user(self, user_id: str) -> None:
        try:
            await self._r.delete(_USER_KEY + user_id)
        except Exception:
            # 占位有 TTL 兜底,删除失败不阻塞结束流程
            log.warning("语音用户占位删除失败 user=%s", user_id, exc_info=True)

    async def try_acquire_slot(self) -> bool:
        try:
            # 原子「未满则加一」:超过上限不加并返回失败
            return bool(
                await self._r.eval(
                    self._ACQUIRE_SLOT_LUA, 1, _SLOT_KEY, self._max_concurrent, self._ttl
                )
            )
        except Exception as e:
            # 准入 fail-closed:读不到即拒绝(与限流器 fail-open 相反是有意的)
            log.warning("语音并发槽位不可用: %s", type(e).__name__)
            raise

    async def release_slot(self) -> None:
        try:
            await self._r.eval(self._RELEASE_SLOT_LUA, 1, _SLOT_KEY)
        except Exception:
            # 计数带兜底过期,减一失败不阻塞结束流程
            log.warning("语音并发槽位释放失败", exc_info=True)


class RedisTicketStore(TicketStore):
    """Redis 票据:取出即删(GETDEL,原子一次性),绑定本人。"""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def issue(self, user_id: str, call_id: str, ttl_sec: int) -> Ticket:
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"u": user_id, "c": call_id})
        await self._r.set(_TICKET_KEY + token, payload, ex=ttl_sec)
        return Ticket(token=token, user_id=user_id, call_id=call_id, ttl_sec=ttl_sec)

    async def consume(self, token: str) -> Ticket | None:
        # GETDEL 原子取出即删:重复使用一律落空;归属由通话表核对
        raw = await self._r.getdel(_TICKET_KEY + token)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return Ticket(
            token=token, user_id=str(data.get("u") or ""), call_id=str(data.get("c") or "")
        )


class HttpxSignalingGateway:
    """httpx 牵线:原样转发连接请求并返回应答;失败统一 SignalingFailed 不透传原文。"""

    def __init__(self, settings: Settings) -> None:
        # 接入地址来自部署配置,不是用户输入(D10)
        self._url = (settings.voice_realtime_base_url or "").rstrip("/")
        self._key = settings.voice_api_key or ""
        self._model = settings.voice_model or ""

    async def exchange(self, offer: bytes) -> bytes:
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                timeout=SIGNALING_TIMEOUT_SEC,
                follow_redirects=False,  # 不跟随重定向(D10)
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/sdp",
                },
            ) as client:
                resp = await client.post(self._url, params={"model": self._model}, content=offer)
        except httpx.HTTPError:
            # 网络/超时/状态异常统一收敛:日志只记类别,不含请求与应答正文
            log.warning("语音牵线网络失败")
            raise SignalingFailed("语音服务暂时不可用") from None
        if resp.status_code != 200 or len(resp.content) > 256 * 1024:
            log.warning("语音牵线上游拒绝 status=%s", resp.status_code)
            raise SignalingFailed("语音服务暂时不可用") from None
        return resp.content
