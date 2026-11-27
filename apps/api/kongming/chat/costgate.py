"""问答限流与用量配额。

登录用户按 (租户, 用户) 维度限流,并按日/月统计调用次数;公开嵌入按
(助手标识, 客户端 IP) 维度限流。速率用 Redis 滑动窗口,Redis 不可用放行;
配额读数失败则拒绝;失败调用也计入配额。

公开嵌入的 IP 从受信代理解析后的请求上下文读取。
限流只保护配额,不替代权限、租户隔离或 SQL 防火墙。
"""

import datetime as dt
import logging
from contextvars import ContextVar
from typing import Protocol

from kongming.quota import check_quota

_TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Shanghai(与 billing 同口径)

log = logging.getLogger(__name__)

# 达预警线值(请求域;与 audit 的 client_ip_var 同范式——横切回传响应头,
# 免穿 service→outcome 多层 dataclass)。每请求独立 task,无跨请求泄漏。
quota_warn_var: ContextVar[str] = ContextVar("kongming_chat_quota_warn", default="")


class RateLimiterPort(Protocol):
    """限流端口(RedisRateLimiter 满足;鸭子类型便于测试注入)。

    allow=固定窗口(工具配额);allow_sliding=近似滑动窗口(问答速率闸,
    跨窗口平滑,消除窗口切换的完整额度重置)。
    """

    async def allow(self, key: str, *, limit: int, window_sec: int) -> bool: ...

    async def allow_sliding(self, key: str, *, limit: int, window_sec: int) -> bool: ...


class UsageCounterPort(Protocol):
    """租户窗口调用计数端口(chat 域组合根绑定 SqlLlmUsageRepo 适配)。"""

    async def count_calls(
        self, *, tenant_id: str, day_start: dt.datetime, month_start: dt.datetime
    ) -> tuple[int, int]: ...


class OperLoggerPort(Protocol):
    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


class QuotaSourcePort(Protocol):
    """租户配额来源(managed=控制面权益快照;standalone 不注入走部署配置)。"""

    async def limits(self, tenant_id: str) -> object: ...


class ChatRateLimited(Exception):
    """429 速率超限(detail=CHAT_RATE_LIMITED)。"""


class ChatQuotaExceeded(Exception):
    """429 配额超限(detail=CHAT_QUOTA_EXCEEDED;warn 供 X-Quota-Warning)。"""

    def __init__(self, message: str, *, warn: str = "") -> None:
        super().__init__(message)
        self.warn = warn


class ChatQuotaUnavailable(Exception):
    """503 配额读数失败(fail-closed 拒绝的落库原因;detail=CHAT_QUOTA_UNAVAILABLE)。"""


def day_window(now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """Asia/Shanghai 当月/当日起点(纯函数,可测)。"""
    local = now.astimezone(_TZ)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    return day_start, month_start


class ChatCostGate:
    """问答成本闸(零框架;端口经组合根注入)。

    limit<=0 的维度一律不限(与 check_quota 口径一致)。
    """

    def __init__(
        self,
        *,
        limiter: RateLimiterPort,
        counter: UsageCounterPort | None = None,  # None=配额维不可用(bot 面)
        oper_logger: OperLoggerPort | None = None,
        rate_per_minute: int = 20,
        bot_rate_per_minute: int = 30,  # 公开嵌入面独立预算
        quota_daily_calls: int = 500,
        quota_monthly_calls: int = 10000,
        quota_source: QuotaSourcePort | None = None,
        now: dt.datetime | None = None,  # 测试注入口(缺省取系统时钟)
    ) -> None:
        self._limiter = limiter
        self._counter = counter
        self._oper = oper_logger
        self._rate = rate_per_minute
        self._bot_rate = bot_rate_per_minute
        self._daily = quota_daily_calls
        self._monthly = quota_monthly_calls
        self._quota_source = quota_source
        self._now = now

    async def _limits(self, tenant_id: str) -> tuple[int, int]:
        """当前配额上限:注入快照来源时以其为准;缺失/异常 fail-closed。"""
        if self._quota_source is None:
            return self._daily, self._monthly
        try:
            limits = await self._quota_source.limits(tenant_id)
        except Exception as e:
            await self._audit("chat.quota_source_failed", tenant_id, {"error": str(e)[:200]})
            raise ChatQuotaUnavailable("CHAT_QUOTA_UNAVAILABLE") from e
        return int(getattr(limits, "calls_daily", 0)), int(getattr(limits, "calls_monthly", 0))

    async def _check_rate(self, *, tenant_id: str, user_id: str) -> None:
        """速率闸(租户,用户)近似滑动窗口;limit<=0 不限,存储异常 fail-open。"""
        if self._rate <= 0:
            return
        try:
            allowed = await self._limiter.allow_sliding(
                f"chat:{tenant_id}:{user_id}", limit=self._rate, window_sec=60
            )
        except Exception:  # 限流存储异常=fail-open 放行(同口径)
            log.exception("速率闸存储异常,fail-open 放行")
            allowed = True
        if not allowed:
            raise ChatRateLimited("CHAT_RATE_LIMITED")

    async def _check_quota_dim(
        self, tenant_id: str, *, dimension: str, used: int, limit: int
    ) -> None:
        """单维配额判定:超限拒绝;达预警线仅记审计。limit<=0 不限。"""
        if limit <= 0:
            return
        v = check_quota(used=used, limit=limit, warn_pct=80, dimension="CALLS")
        if not v.allowed:
            await self._reject(tenant_id, dimension, used, limit)
        if v.warn:
            await self._reject(tenant_id, dimension, used, limit, warn_only=True)

    @staticmethod
    def _warn_header(*, used_day: int, used_month: int, daily: int, monthly: int) -> str:
        """预警头:任一维达线即带(维度: 已用/限额),逗号连接。"""
        warns: list[str] = []
        if daily > 0 and used_day * 100 >= daily * 80:
            warns.append(f"CHAT_DAILY_CALLS:{used_day}/{daily}")
        if monthly > 0 and used_month * 100 >= monthly * 80:
            warns.append(f"CHAT_MONTHLY_CALLS:{used_month}/{monthly}")
        return ",".join(warns)

    async def check_login(self, *, tenant_id: str, user_id: str) -> str:
        """登录用户面双闸:速率(租户,用户)→ 配额(租户)。通过返回 warn(可空)。"""
        await self._check_rate(tenant_id=tenant_id, user_id=user_id)
        if self._counter is None:
            return ""  # 无配额维(bot 面):仅速率闸
        daily, monthly = await self._limits(tenant_id)
        day_start, month_start = day_window(self._now or dt.datetime.now(_TZ))
        try:
            used_day, used_month = await self._counter.count_calls(
                tenant_id=tenant_id, day_start=day_start, month_start=month_start
            )
        except Exception as e:
            await self._audit("chat.quota_read_failed", tenant_id, {"error": str(e)[:200]})
            raise ChatQuotaUnavailable("CHAT_QUOTA_UNAVAILABLE") from e
        # 日维先于月维(拒绝/预警审计顺序不变)
        await self._check_quota_dim(
            tenant_id, dimension="CHAT_DAILY_CALLS", used=used_day, limit=daily
        )
        await self._check_quota_dim(
            tenant_id, dimension="CHAT_MONTHLY_CALLS", used=used_month, limit=monthly
        )
        # 预警头经 contextvar 回传路由落响应头
        warn = self._warn_header(
            used_day=used_day, used_month=used_month, daily=daily, monthly=monthly
        )
        quota_warn_var.set(warn)
        return warn

    async def _audit(self, action: str, tenant_id: str, detail: dict[str, object]) -> None:
        """审计写入(任何失败仅吞掉——闸决策不因审计不可用而改变)。"""
        if self._oper is None:
            return
        try:
            await self._oper.log(action=action, target=tenant_id, detail=detail)
        except Exception:  # 审计旁路不阻断/不翻转闸语义
            log.warning("chat 成本闸审计写入失败(忽略) action=%s", action, exc_info=True)

    async def _reject(
        self,
        tenant_id: str,
        dimension: str,
        used: int,
        limit: int,
        *,
        warn_only: bool = False,
    ) -> None:
        """审计(超限拒绝/预警同口径落);warn_only=True 仅记录不抛。"""
        await self._audit(
            "chat.quota_warn" if warn_only else "chat.quota_reject",
            tenant_id,
            {"dimension": dimension, "used": used, "limit": limit},
        )
        if warn_only:
            return
        raise ChatQuotaExceeded(f"CHAT_QUOTA_EXCEEDED: {dimension}", warn=dimension)

    async def check_bot(self, *, bot_key: str, client_ip: str | None) -> None:
        """公开嵌入面速率闸((助手标识, 客户端 IP) 维度;无用户/租户可用)。
        IP 缺失(None,非 HTTP 请求路径)= 不启用该维 key 退化为仅 bot_key。"""
        if self._bot_rate <= 0:
            return
        key = f"chatbot:{bot_key}:{client_ip or 'noip'}"
        try:
            allowed = await self._limiter.allow_sliding(key, limit=self._bot_rate, window_sec=60)
        except Exception:  # 同上:fail-open
            log.exception("bot 速率闸存储异常,fail-open 放行")
            allowed = True
        if not allowed:
            raise ChatRateLimited("CHAT_RATE_LIMITED")
