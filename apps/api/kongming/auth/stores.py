"""auth 域 Redis 实现(唯一 import redis 的 auth 文件)。

refresh 家族三态(D2):
  auth:rt:{jti}      → fid      活跃票(EX=refresh TTL)
  auth:rt:x:{jti}    → fid      已轮换墓碑(EX=原 TTL,复用检测依据)
  auth:rfam:bad:{fid}→ 1        家族吊销标记(EX=refresh TTL)
  auth:rfams:{uid}   → fid 集合  用户的家族索引(改密/全端登出)
登录锁定:
  auth:fail:{username} → 计数(INCR,首败设 EX)
  auth:lock:{username} → 1      锁标记(EX=锁定时长)
联邦 state:
  auth:ostate:{state} → JSON{provider, code_verifier}(GETDEL 一次性消费)
"""

import json

from redis.asyncio import Redis

_RT = "auth:rt:"
_RT_X = "auth:rt:x:"
_RFAM_BAD = "auth:rfam:bad:"
_RFAMS = "auth:rfams:"
_FAIL = "auth:fail:"
_LOCK = "auth:lock:"
_OSTATE = "auth:ostate:"

# 原子轮换:GETDEL 活跃票并立墓碑,单脚本闭环——若拆成"读活跃→删活跃→写墓碑"多轮
# 往返,并发同票刷新会双双通过检查,产生两条有效链,静默破坏 ADR-003 的轮换失效保证。
# 返回 {活跃 fid, 墓碑 fid}:消费成功={fid, nil};命中墓碑(复用)={nil, fid};两皆无=过期/伪造。
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


class RedisLoginAttemptStore:
    def __init__(self, r: Redis) -> None:
        self._r = r

    async def incr_fails(self, username: str, window_sec: int) -> int:
        key = _FAIL + username
        async with self._r.pipeline(transaction=True) as p:
            p.incr(key)
            # 仅首次失败设窗口期(EXPIRE 的 NX 选项:已有 TTL 不刷新,防滑动窗口拉长)
            p.expire(key, window_sec, nx=True)
            n, _ = await p.execute()
        return int(n)

    async def clear_fails(self, username: str) -> None:
        await self._r.delete(_FAIL + username)

    async def lock(self, username: str, lock_sec: int) -> None:
        await self._r.set(_LOCK + username, 1, ex=lock_sec)

    async def locked_ttl(self, username: str) -> int:
        ttl: int = await self._r.ttl(_LOCK + username)
        return max(0, ttl)


class RedisOAuthStateStore:
    def __init__(self, r: Redis) -> None:
        self._r = r

    async def put(
        self, state: str, *, provider: str, code_verifier: str | None, ttl_sec: int
    ) -> None:
        payload = json.dumps({"provider": provider, "code_verifier": code_verifier})
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
