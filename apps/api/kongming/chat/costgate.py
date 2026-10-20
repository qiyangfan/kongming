"""问答成本控制(062-chat-cost-control):登录用户面双闸 + 公开嵌入面单闸。

倒挂背景:成本控制此前只覆盖已有计费关系的 api-key 调用方(034 配额切面),
而匿名访客经公开嵌入页、任意登录用户刷问答却是零成本控制(单次问答烧
5+ 次 LLM 调用)。

本模块(零框架,宪法 Article II):
- **速率闸**(fail-open):Redis 近似滑动窗口(跨窗口平滑,窗口切换不再
  立即发放完整新额度),_LIMITER 不可用放行——限流是
  配额保护层而非安全闸(安全闸=权限点/租户/防火墙各自独立,与 019 同口径);
- **配额闸**(fail-closed):日/月按次,已用量 = sys_llm_usage 行数按窗口
  count(不新增用量表,避免与 055 对账重复建账);读数异常拒绝并落审计
  ——放行即失控窗口;
- **配额口径**:失败尝试计入(072「用量每尝试一条」+ 038「失败调用也采集」
  既有语义)——按次配额统计的是「调用次数」而非「成功次数」,不因本变更
  引入偏差(072 任务 3.1 的口径移交在此闭合)。

公开嵌入面(无用户/租户维度可用):(助手标识, 客户端 IP) 维度速率闸,
IP 取自 ClientIpMiddleware contextvar(经 --forwarded-allow-ips 约束,
非客户端可伪造),fail-open。
"""

import datetime as dt
import logging
from contextvars import ContextVar
from typing import Protocol

from kongming.billing.models import check_quota

_TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Shanghai(与 billing 031 同口径)

log = logging.getLogger(__name__)

# 062:达预警线值(请求域;与 audit 的 client_ip_var 同范式——横切回传响应头,
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
        now: dt.datetime | None = None,  # 测试注入口(缺省取系统时钟)
    ) -> None:
        self._limiter = limiter
        self._counter = counter
        self._oper = oper_logger
        self._rate = rate_per_minute
        self._bot_rate = bot_rate_per_minute
        self._daily = quota_daily_calls
        self._monthly = quota_monthly_calls
        self._now = now

    async def check_login(self, *, tenant_id: str, user_id: str) -> str:
        """登录用户面双闸:速率(租户,用户)→ 配额(租户)。通过返回 warn(可空)。"""
        if self._rate > 0:
            try:
                allowed = await self._limiter.allow_sliding(
                    f"chat:{tenant_id}:{user_id}", limit=self._rate, window_sec=60
                )
            except Exception:  # noqa: BLE001  # 限流存储异常=fail-open 放行(019 同口径)
                log.error("速率闸存储异常,fail-open 放行", exc_info=True)
                allowed = True
            if not allowed:
                raise ChatRateLimited("CHAT_RATE_LIMITED")
        if self._counter is None:
            return ""  # 无配额维(bot 面):仅速率闸
        day_start, month_start = day_window(self._now or dt.datetime.now(_TZ))
        try:
            used_day, used_month = await self._counter.count_calls(
                tenant_id=tenant_id, day_start=day_start, month_start=month_start
            )
        except Exception as e:  # noqa: BLE001  读数失败=fail-closed 拒绝(不裸 500)
            await self._audit("chat.quota_read_failed", tenant_id, {"error": str(e)[:200]})
            raise ChatQuotaUnavailable("CHAT_QUOTA_UNAVAILABLE") from e
        if self._daily > 0:
            v = check_quota(
                used=used_day,
                limit=self._daily,
                warn_pct=80,
                dimension="CALLS",
            )
            if not v.allowed:
                await self._reject(tenant_id, "CHAT_DAILY_CALLS", used_day, self._daily)
            if v.warn:
                await self._reject(
                    tenant_id,
                    "CHAT_DAILY_CALLS",
                    used_day,
                    self._daily,
                    warn_only=True,
                )
        if self._monthly > 0:
            v = check_quota(
                used=used_month,
                limit=self._monthly,
                warn_pct=80,
                dimension="CALLS",
            )
            if not v.allowed:
                await self._reject(tenant_id, "CHAT_MONTHLY_CALLS", used_month, self._monthly)
            if v.warn:
                await self._reject(
                    tenant_id,
                    "CHAT_MONTHLY_CALLS",
                    used_month,
                    self._monthly,
                    warn_only=True,
                )
        # 预警头:任一维达线即带(维度: 已用/限额);contextvar 回传路由落响应头
        warns: list[str] = []
        if self._daily > 0 and used_day * 100 >= self._daily * 80:
            warns.append(f"CHAT_DAILY_CALLS:{used_day}/{self._daily}")
        if self._monthly > 0 and used_month * 100 >= self._monthly * 80:
            warns.append(f"CHAT_MONTHLY_CALLS:{used_month}/{self._monthly}")
        warn = ",".join(warns)
        quota_warn_var.set(warn)
        return warn

    async def _audit(self, action: str, tenant_id: str, detail: dict[str, object]) -> None:
        """审计写入(任何失败仅吞掉——闸决策不因审计不可用而改变)。"""
        if self._oper is None:
            return
        try:
            await self._oper.log(action=action, target=tenant_id, detail=detail)
        except Exception:  # noqa: BLE001  # 审计旁路不阻断/不翻转闸语义
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
        except Exception:  # noqa: BLE001  # 同上:fail-open
            log.error("bot 速率闸存储异常,fail-open 放行", exc_info=True)
            allowed = True
        if not allowed:
            raise ChatRateLimited("CHAT_RATE_LIMITED")
