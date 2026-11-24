"""在途轮的跨实例登记与取消转发(share-turn-registry)。

键 `chat:turn:{trace_id}` 的值记录归属用户、持有实例地址与注销凭据;TTL 即登记
存活期,须大于「单轮 agent 流上限 + 收敛余量」(见 KM_TURN_REGISTRY_TTL_SEC 与
KM_AGENT_TIMEOUT_SEC 的不变量)。Redis 不可用一律降级:登记失败=该轮仅本实例
可见,定位/转发失败=调用方走落库兜底——不伪造终态,也不比今天更糟。
"""

import json
import logging
import uuid
from dataclasses import replace

import httpx
from redis.asyncio import Redis

from kongming.chat.agent_client import ABORT_TIMEOUT_SEC
from kongming.chat.domain_errors import TurnNotFound
from kongming.chat.inflight import CANCEL_SETTLE_TIMEOUT_SEC, TurnSettlement
from kongming.chat.ports import TurnLocation
from kongming.config import Settings

log = logging.getLogger(__name__)

_TURN_KEY = "chat:turn:"
_INTERNAL_CANCEL_PATH = "/internal/chat/turn-cancel"

# 持有侧最坏耗时 = 通知 3s + 等收敛 5s + 硬取消后再等 5s,加 3s 余量
FORWARD_TIMEOUT_SEC = ABORT_TIMEOUT_SEC + 2 * CANCEL_SETTLE_TIMEOUT_SEC + 3.0

# 注销脚本:按凭据(值内 nonce)匹配才删。分开 GET 再 DEL 会让同名轮(客户端自带
# trace_id 撞号)的先结束者删掉后者的登记;同一 trace 只应被持凭据者注销。
# 值损坏(pcall 兜底)按不匹配处理——locate 对损坏值同样按不在途回答。
_RELEASE_LUA = """
local raw = redis.call('GET', KEYS[1])
if raw then
  local ok, data = pcall(cjson.decode, raw)
  if ok and data['n'] == ARGV[1] then
    return redis.call('DEL', KEYS[1])
  end
end
return 0
"""


class RedisTurnFederation:
    """Redis 实现的跨实例在途协作(基础设施侧,领域只认 TurnFederation 端口)。"""

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._r = redis
        # 自报地址逐实例唯一(容器主机名/pod 地址):共享服务名会让自环防护误判
        self._self_url = settings.api_self_base_url.rstrip("/")
        self._ttl = settings.turn_registry_ttl_sec
        self._token = settings.internal_token or ""

    async def claim(self, *, trace_id: str, user_id: str) -> str | None:
        """登记本实例持有该轮,返回注销凭据;登记失败返回 None(该轮仅本实例可见)。"""
        token = uuid.uuid4().hex
        payload = json.dumps({"u": user_id, "url": self._self_url, "n": token}, ensure_ascii=False)
        try:
            await self._r.set(_TURN_KEY + trace_id, payload, ex=self._ttl)
        except Exception:
            # 降级放行:登记失败不阻断该轮,取消回落到单实例语义
            log.exception("在途轮跨实例登记失败 trace_id=%s", trace_id)
            return None
        return token

    async def release(self, *, trace_id: str, token: str) -> None:
        """按凭据注销(值匹配才删;不匹配=已被同名轮顶替,不删他人登记)。"""
        try:
            await self._r.eval(_RELEASE_LUA, 1, _TURN_KEY + trace_id, token)
        except Exception:
            log.exception("在途轮跨实例注销失败 trace_id=%s", trace_id)

    async def locate(self, *, trace_id: str) -> TurnLocation | None:
        """按轮次标识取归属与持有地址;读取失败或键不存在返回 None。"""
        try:
            raw = await self._r.get(_TURN_KEY + trace_id)
        except Exception:
            log.exception("在途轮跨实例定位失败 trace_id=%s", trace_id)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return TurnLocation(user_id=str(data["u"]), base_url=str(data["url"]))
        except (ValueError, TypeError, KeyError):
            log.warning("在途轮登记值损坏,按不在途处理 trace_id=%s", trace_id)
            return None

    async def cancel_remote(self, *, trace_id: str, user_id: str) -> TurnSettlement | None:
        """把取消转给持有该轮的实例并等它收敛;无处可转返回 None(调用方走落库兜底)。"""
        location = await self.locate(trace_id=trace_id)
        if location is None:
            return None
        if location.user_id != user_id:
            # 归属不符:与不存在同形,不转发(转发也只会被持有侧按同一规则拒绝)
            raise TurnNotFound(trace_id)
        if location.base_url == self._self_url:
            # 指向本实例却不在途:只可能是实例重启残留的陈旧登记,自我转发会自环
            log.info("在途轮登记指向本实例但无该轮,按陈旧登记处理 trace_id=%s", trace_id)
            return None
        return await self._forward(location, trace_id=trace_id, user_id=user_id)

    async def _forward(
        self, location: TurnLocation, *, trace_id: str, user_id: str
    ) -> TurnSettlement | None:
        """经内部面把取消转给持有该轮的实例;任何失败都返回 None,由调用方走兜底。"""
        try:
            async with self._client(location.base_url) as client:
                resp = await client.post(
                    _INTERNAL_CANCEL_PATH, json={"trace_id": trace_id, "user_id": user_id}
                )
        except httpx.HTTPError as e:
            log.warning("跨实例取消转发失败 trace_id=%s: %s", trace_id, e)
            return None
        if resp.status_code != 200:
            # 404=该轮在持有侧也已不在途(刚结束);按落库兜底如实回答
            log.info("跨实例取消转发未成功 trace_id=%s: HTTP %s", trace_id, resp.status_code)
            return None
        try:
            data = resp.json()
            outcome = str(data["outcome"])
            message_id = data.get("message_id")
        except (ValueError, TypeError, KeyError, AttributeError):
            log.warning("跨实例取消转发应答损坏 trace_id=%s", trace_id)
            return None
        if outcome not in ("CANCELLED", "COMPLETED"):
            log.warning("跨实例取消转发应答非常态 trace_id=%s: %s", trace_id, outcome)
            return None
        base = (
            TurnSettlement("CANCELLED") if outcome == "CANCELLED" else TurnSettlement("COMPLETED")
        )
        return replace(base, message_id=None if message_id is None else str(message_id))

    def _client(self, base_url: str) -> httpx.AsyncClient:
        """出站客户端(不采环境代理,与 agent 回调同口径;超时覆盖持有侧最坏耗时)。"""
        return httpx.AsyncClient(
            trust_env=False,
            base_url=base_url,
            timeout=FORWARD_TIMEOUT_SEC,
            headers={"X-Internal-Token": self._token},
        )
