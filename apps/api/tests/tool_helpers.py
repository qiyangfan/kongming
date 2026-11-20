# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
"""工具域测试共享辅助(TCP 存根 + 租户/用户/权限装配)。

放非 test_ 前缀模块,避免被 pytest 收集;test_websearch_domain 与
test_toolgateway 共用。
"""

import contextlib
import socket
import threading
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class _Stub:
    """一次性 TCP 存根:每个连接交给 handler(conn, 第几次),记录连接次数。

    刻意不用 ASGI/uvicorn:被测客户端自建 AsyncClient,需要的是真实 TCP
    失败面(拒连/accept 后关连/响应形态),裸 socket 存根最贴近。
    """

    def __init__(self, handler: Callable[[socket.socket, int], None]) -> None:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._handler = handler
        self._n = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @property
    def connections(self) -> int:
        with self._lock:
            return self._n

    def _loop(self) -> None:
        self._srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with self._lock:
                self._n += 1
                nth = self._n
            try:
                conn.settimeout(5)
                self._handler(conn, nth)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    conn.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._srv.close()


def _http_response(
    body: str, *, status: int = 200, content_type: str = "application/json"
) -> bytes:
    return (
        f"HTTP/1.1 {status} OK\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body.encode())}\r\nConnection: close\r\n\r\n{body}"
    ).encode()


def _req(tool: str, args: dict[str, object], **kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trace_id": f"tr-{uuid.uuid4().hex[:12]}",
        "tenant_key": "ek",
        "tool_name": tool,
        "args": args,
    }
    base.update(kw)
    return base


async def _mk_tenant(s: AsyncSession, key: str) -> str:
    tid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name, status)"
                " VALUES (gen_random_uuid(), :k, :k, 'ACTIVE') RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    await s.commit()
    return str(tid)


async def _mk_role_with_perms(s: AsyncSession, key: str, codes: list[str]) -> str:
    rid = (
        await s.execute(
            text(
                "INSERT INTO public.sys_role (id, role_key, role_name, data_scope)"
                " VALUES (gen_random_uuid(), :k, :k, 'SELF') RETURNING id"
            ),
            {"k": key},
        )
    ).scalar_one()
    for code in codes:
        await s.execute(
            text(
                "INSERT INTO public.sys_permission (id, perm_code, perm_name)"
                " VALUES (gen_random_uuid(), :c, :c) ON CONFLICT DO NOTHING"
            ),
            {"c": code},
        )
        await s.execute(
            text(
                "INSERT INTO public.sys_role_permission (role_id, permission_id)"
                " SELECT :r, p.id FROM public.sys_permission p WHERE p.perm_code = :c"
                " ON CONFLICT DO NOTHING"
            ),
            {"r": rid, "c": code},
        )
    await s.commit()
    return str(rid)


async def _bind(s: AsyncSession, table: str, user_id: str, target_id: str) -> None:
    col = "role_id" if table == "sys_user_role" else "tenant_id"
    await s.execute(
        text(
            f"INSERT INTO public.{table} (user_id, {col}) VALUES (:u, :t)"  # noqa: S608  # 测试常量
        ),
        {"u": user_id, "t": target_id},
    )
    await s.commit()


async def _user_with_tools(
    s: AsyncSession, make_user: Callable[..., Any], username: str, codes: list[str]
) -> str:
    uid = await make_user(username)
    rid = await _mk_role_with_perms(s, f"role_{username}", codes)
    tid = (
        await s.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key=:k"), {"k": "ek"})
    ).scalar_one()
    await _bind(s, "sys_user_role", uid, rid)
    await _bind(s, "sys_user_tenant", uid, str(tid))
    return uid
