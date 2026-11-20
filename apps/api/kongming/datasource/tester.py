"""测试连接:asyncpg 直连 SELECT 1 即关。

失败 reason 收敛为驱动错误类别文案(超时/无法连接/认证失败/库不存在),
永不回显密码与明文连接串;不落库、不建池、不落审计(无状态操作)。
"""

import asyncio
import logging
from typing import Protocol, cast

import asyncpg

from kongming.datasource.models import TestConnectionResult

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SEC = 3  # spec「测试连接」:连接超时 3s


class _PgConn(Protocol):
    """asyncpg 连接的最小类型面:asyncpg 源码未完整注解,pyright strict 下
    引用 asyncpg.Connection 本身即携带 Unknown;本测试仅消费这两个成员,
    经 Protocol 收窄(仅类型层面,运行时即原 asyncpg 连接对象)。"""

    async def fetchval(self, query: str) -> object: ...
    async def close(self) -> None: ...


def _classify(e: Exception) -> str:
    """驱动异常 → 用户可见类别文案(不含密码/连接串等敏感细节)。"""
    if isinstance(e, (TimeoutError, asyncio.TimeoutError)):
        return "连接超时(3s 未建立)"
    if isinstance(e, asyncpg.InvalidPasswordError):
        return "认证失败:用户名或密码错误"
    if isinstance(e, asyncpg.InvalidCatalogNameError):
        return "数据库不存在"
    if isinstance(e, asyncpg.InvalidAuthorizationSpecificationError):
        return "认证失败:账号无登录权限"
    if isinstance(e, OSError):
        # 拒连/网络不可达等底层错误(ConnectionRefusedError 等,消息不含凭证)
        return "无法连接:主机或端口不可达"
    # 驱动类别名(无敏感信息),兜底不区分具体类型
    return f"连接失败:{type(e).__name__}" if isinstance(e, asyncpg.PostgresError) else "连接失败"


class AsyncpgConnectionTester:
    """ports.ConnectionTester 的 asyncpg 实现。"""

    async def test(
        self, *, host: str, port: int, database: str, username: str, password: str
    ) -> TestConnectionResult:
        conn: _PgConn | None = None
        try:
            # asyncpg.connect 签名未注解(strict 判部分未知),成员访问行豁免;
            # 经 _PgConn Protocol 收窄后消费(仅类型断言,运行时为原连接对象)
            c = cast(
                "_PgConn",
                await asyncpg.connect(  # pyright: ignore[reportUnknownMemberType]  # asyncpg 未注解
                    host=host,
                    port=port,
                    database=database,
                    user=username,
                    password=password,
                    timeout=_CONNECT_TIMEOUT_SEC,
                ),
            )
            conn = c
            await c.fetchval("SELECT 1")
        except Exception as e:  # noqa: BLE001  # 类别已收敛,驱动异常不向外抛
            return TestConnectionResult(ok=False, reason=_classify(e))
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # 断链上 close 可再抛,不得顶掉已构造的结果
                    log.debug("测试连接 close 二次异常(已忽略)", exc_info=True)
        return TestConnectionResult(ok=True)
