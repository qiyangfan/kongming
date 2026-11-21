"""认证域的 Redis 存储实现。

refresh 票据以活跃键、轮换墓碑和家族吊销标记管理；用户索引支持全端登出。
联邦 state 通过 GETDEL 一次性消费。"""

import json

from redis.asyncio import Redis

_RT = "auth:rt:"
_RT_X = "auth:rt:x:"
_RFAM_BAD = "auth:rfam:bad:"
_RFAMS = "auth:rfams:"
_OSTATE = "auth:ostate:"

# 单条 Lua 脚本原子消费活跃票并写入轮换墓碑。
# 分开读取、删除和写入会使并发刷新都通过校验并生成有效票，
# 因而无法保证旧 refresh 票据失效。
_CONSUME_LUA = """
local f = redis.call('GETDEL', KEYS[1])
if f then
  redis.call('SET', KEYS[2], f, 'EX', ARGV[1])
  return {f, false}
end
local t = redis.call('GET', KEYS[2])
if t then
  return {false, t}
end
return {false, false}
"""


class RedisRefreshFamilyStore:
    def __init__(self, r: Redis) -> None:
        self._r = r

    async def issue(self, user_id: str, fid: str, jti: str, ttl_sec: int) -> None:
        async with self._r.pipeline(transaction=True) as p:
            p.set(_RT + jti, fid, ex=ttl_sec)
            p.sadd(_RFAMS + user_id, fid)
            p.expire(_RFAMS + user_id, ttl_sec)
            await p.execute()

    async def consume(self, jti: str, ttl_sec: int) -> tuple[str | None, str | None]:
        """原子消费活跃票并立墓碑(轮换的唯一判定动作)。

        并发下同票只有一个调用方拿到 rotated;败者读到墓碑,按 spec「复用即吊销
        家族」处理——多标签页毫秒级同票竞刷会触发重新登录,属契约语义的既定代价。
        """
        result: list[str | None] = await self._r.eval(
            _CONSUME_LUA, 2, _RT + jti, _RT_X + jti, ttl_sec
        )
        rotated, tomb = result[0], result[1]
        return (None if rotated is None else str(rotated), None if tomb is None else str(tomb))

    async def is_revoked_family(self, fid: str) -> bool:
        return bool(await self._r.exists(_RFAM_BAD + fid))

    async def revoke_family(self, fid: str, ttl_sec: int) -> None:
        await self._r.set(_RFAM_BAD + fid, 1, ex=ttl_sec)

    async def revoke_all_user_families(self, user_id: str, ttl_sec: int) -> None:
        key = _RFAMS + user_id
        fids = {str(f) for f in await self._r.smembers(key)}
        if not fids:
            return
        async with self._r.pipeline(transaction=True) as p:
            for fid in fids:
                p.set(_RFAM_BAD + fid, 1, ex=ttl_sec)
            # 索引集合保留(随 TTL 自然过期):若删除,smembers 与吊销之间新登录的家族
            # 会既逃过吊销又丢掉索引追踪,竞态后果更糟
            await p.execute()


class RedisOAuthStateStore:
    def __init__(self, r: Redis) -> None:
        self._r = r

    async def put(
        self,
        state: str,
        *,
        provider: str,
        code_verifier: str | None,
        ttl_sec: int,
        nonce: str | None = None,
    ) -> None:
        payload = json.dumps(
            {"provider": provider, "code_verifier": code_verifier, "nonce": nonce or ""}
        )
        await self._r.set(_OSTATE + state, payload, ex=ttl_sec)

    async def take(self, state: str) -> dict[str, str] | None:
        raw = await self._r.getdel(_OSTATE + state)
        if raw is None:
            return None
        try:
            data: dict[str, str] = json.loads(raw)
        except ValueError:
            # 值损坏(外部写入/序列化漂移)= state 无效——归一 None 让调用方
            # 走既有 StateInvalid 拒绝(401 语义),不把存储损坏泄漏成 500
            return None
        return data
