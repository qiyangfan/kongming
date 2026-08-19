"""chat 持久化(基础设施层):chat_session/chat_message 读写。

所有查询经 SQLAlchemy text() 绑定参数;会话归属判定在此收口
(不存在与越权同形返回 None,不泄露信息,chat spec)。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def find_bound_tenant(self, *, user_id: str, tenant_key: str) -> str | None:
        """显式租户白名单校验:命中(绑定 + ACTIVE)返回 tenant_id,否则 None。"""
        tid = (
            await self._s.execute(
                text(
                    "SELECT t.id FROM public.sys_tenant t"
                    " JOIN public.sys_user_tenant ut ON ut.tenant_id = t.id"
                    " WHERE t.tenant_key = :k AND ut.user_id = :u AND t.status = 'ACTIVE'"
                ),
                {"k": tenant_key, "u": user_id},
            )
        ).scalar_one_or_none()
        return None if tid is None else str(tid)

    async def find_default_tenant(self, *, user_id: str) -> tuple[str, str] | None:
        """用户默认租户:default 优先,否则最早授予(multitenant spec);返回 (id, key)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT t.id, t.tenant_key FROM public.sys_tenant t"
                    " JOIN public.sys_user_tenant ut ON ut.tenant_id = t.id"
                    " WHERE ut.user_id = :u AND t.status = 'ACTIVE'"
                    " ORDER BY (t.tenant_key = 'default') DESC, ut.granted_at ASC"
                    " LIMIT 1"
                ),
                {"u": user_id},
            )
        ).mappings().one_or_none()
        return None if row is None else (str(row["id"]), str(row["tenant_key"]))

    async def create_session(
        self, *, user_id: str, tenant_id: str, title: str, datasource_key: str
    ) -> str:
        sid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.chat_session (id, user_id, tenant_id, datasource_key, title)"
                " VALUES (:id, :u, :t, :ds, :title)"
            ),
            {"id": sid, "u": user_id, "t": tenant_id, "ds": datasource_key, "title": title},
        )
        return sid

    async def find_session_owned(
        self, *, session_id: str, user_id: str, tenant_id: str
    ) -> str | None:
        """归属校验:命中(同用户同租户)返回 session_id,否则 None(不存在/越权同形)。"""
        sid = (
            await self._s.execute(
                text(
                    "SELECT id FROM public.chat_session"
                    " WHERE id = :id AND user_id = :u AND tenant_id = :t"
                ),
                {"id": session_id, "u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        return None if sid is None else str(sid)  # asyncpg 返回 UUID 对象,统一 str

    async def touch_session(self, session_id: str) -> None:
        await self._s.execute(
            text("UPDATE public.chat_session SET updated_at = now() WHERE id = :id"),
            {"id": session_id},
        )

    async def list_sessions(
        self, *, user_id: str, tenant_id: str, limit: int
    ) -> list[dict[str, object]]:
        """本人本租户会话列表,updated_at 倒序(id DESC 作时序 tiebreak)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, title, updated_at FROM public.chat_session"
                    " WHERE user_id = :u AND tenant_id = :t"
                    " ORDER BY updated_at DESC, id DESC LIMIT :n"
                ),
                {"u": user_id, "t": tenant_id, "n": limit},
            )
        ).mappings().all()
        return [
            {"id": str(r["id"]), "title": str(r["title"]), "updated_at": r["updated_at"]}
            for r in rows
        ]

    async def list_messages(self, *, session_id: str) -> list[dict[str, object]]:
        """会话全部消息,创建时间升序(id ASC tiebreak,与补全取数对偶)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, role, question, sql_text, result_json, summary, status"
                    " FROM public.chat_message WHERE session_id = :s"
                    " ORDER BY created_at ASC, id ASC"
                ),
                {"s": session_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def delete_session(self, *, session_id: str, user_id: str, tenant_id: str) -> bool:
        """归属校验 + 删除一条 SQL(原子,无先查后删竞态窗口);未命中(不存在/
        越权同形)返回 False。消息经 ON DELETE CASCADE 级联清除。"""
        res = await self._s.execute(
            text(
                "DELETE FROM public.chat_session"
                " WHERE id = :id AND user_id = :u AND tenant_id = :t RETURNING id"
            ),
            {"id": session_id, "u": user_id, "t": tenant_id},
        )
        return res.scalar_one_or_none() is not None

    async def insert_message(
        self,
        *,
        session_id: str,
        role: str,
        question: str | None = None,
        sql_text: str | None = None,
        result_json: str | None = None,
        summary: str | None = None,
        status: str,
    ) -> str:
        mid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.chat_message"
                " (id, session_id, role, question, sql_text, result_json, summary, status)"
                " VALUES (:id, :s, :role, :q, :sql, :rj, :sum, :st)"
            ),
            {
                "id": mid,
                "s": session_id,
                "role": role,
                "q": question,
                "sql": sql_text,
                "rj": result_json,
                "sum": summary,
                "st": status,
            },
        )
        return mid

    async def recent_user_questions(self, session_id: str, *, limit: int = 5) -> list[str]:
        """最近 N 条 user 问句,旧→新(chat spec「多轮上下文后端补全」)。

        id(UUID v7)作 tiebreak:同事务/同毫秒多行时序仍确定。
        """
        rows = (
            await self._s.execute(
                text(
                    "SELECT question FROM public.chat_message"
                    " WHERE session_id = :s AND role = 'USER' AND question IS NOT NULL"
                    " ORDER BY created_at DESC, id DESC LIMIT :n"
                ),
                {"s": session_id, "n": limit},
            )
        ).scalars().all()
        return [str(q) for q in reversed(rows)]
