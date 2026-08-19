"""测试连接(infrastructure 层,008 design D6):asyncpg 直连 SELECT 1 即关。

失败 reason 收敛为驱动错误类别文案(超时/无法连接/认证失败/库不存在),
永不回显密码与明文连接串;不落库、不建池、不落审计(无状态操作)。
"""

import asyncio
import logging

import asyncpg

from kongming.datasource.models import TestConnectionResult

log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SEC = 3  # spec「测试连接」:连接超时 3s


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
    if isinstance(e, asyncpg.PostgresError):
        return f"连接失败:{type(e).__name__}"  # 驱动类别名(无敏感信息)
    return "连接失败"


class AsyncpgConnectionTester:
    """ports.ConnectionTester 的 asyncpg 实现。"""

    async def test(
        self, *, host: str, port: int, database: str, username: str, password: str
    ) -> TestConnectionResult:
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(
                host=host,
                port=port,
                database=database,
                user=username,
                password=password,
                timeout=_CONNECT_TIMEOUT_SEC,
            )
            await conn.fetchval("SELECT 1")
        except Exception as e:  # noqa: BLE001  # 类别已收敛,驱动异常不向外抛
            return TestConnectionResult(ok=False, reason=_classify(e))
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001  # 断链上 close 可再抛,不得顶掉已构造的结果(R1-S4)
                    log.debug("测试连接 close 二次异常(已忽略)", exc_info=True)
        return TestConnectionResult(ok=True)
