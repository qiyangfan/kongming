"""chat 持久化(基础设施层):chat_session/chat_message 读写。

所有查询经 SQLAlchemy text() 绑定参数;会话归属判定在此收口
(不存在与越权同形返回 None,不泄露信息,chat spec)。
写路径方法与 commit 经 _store_error_boundary 转译 SQLAlchemyError →
ChatStoreError(013 构造反转:service 领域层由此零 sqlalchemy import)。
"""

import functools
import json
from collections.abc import Callable, Coroutine
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from kongming import tenant
from kongming.chat.models import Suggestion
from kongming.chat.ports import ChatStoreError
from kongming.db import uuid7
from kongming.like import like_pattern


def _store_error_boundary[**P, R](
    fn: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """写路径转译边界:SQLAlchemyError → ChatStoreError。

    只加在 service 容错段覆盖的写方法与 commit 上;读路径不加
    (service 不容错,原异常冒出维持 500 既定语义)。
    返回类型保持 typing.Coroutine:mypy 仅认此形态;pyright 对 async def
    成员推导 CoroutineType,故 ports 侧对应成员以 def+Coroutine 显式
    形态声明(两侧类型系统对该组合均精确匹配)。
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except SQLAlchemyError as e:
            raise ChatStoreError(str(e)) from e

    return wrapper


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def find_bound_tenant(self, *, user_id: str, tenant_key: str) -> str | None:
        """显式租户白名单校验:命中(绑定 + ACTIVE)返回 tenant_id,否则 None。
        单一真理源在 kongming.tenant(B1-B10 收口,五处同构 SQL 唯一定义)。"""
        return await tenant.bound_tenant_id(self._s, user_id=user_id, tenant_key=tenant_key)

    async def find_default_tenant(self, *, user_id: str) -> tuple[str, str] | None:
        """用户默认租户:default 优先,否则最早授予(multitenant spec);返回 (id, key)。"""
        return await tenant.default_tenant(self._s, user_id=user_id)

    @_store_error_boundary
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

    async def find_explain_target(
        self, *, message_id: str, user_id: str, tenant_id: str
    ) -> dict[str, object] | None:
        """执行计划定位(010):消息 JOIN 会话做归属收口(不存在/越权同形 None);
        顺带取会话落库的 datasource_key(快照语义,非切换器实时值,010 design D2)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT m.id, m.sql_text, m.session_id, s.datasource_key"
                    " FROM public.chat_message m"
                    " JOIN public.chat_session s ON s.id = m.session_id"
                    " WHERE m.id = :mid AND s.user_id = :u AND s.tenant_id = :t"
                    " AND m.role = 'ASSISTANT'"  # 审查循环 B5:锚定 assistant 产出消息
                ),
                {"mid": message_id, "u": user_id, "t": tenant_id},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    @_store_error_boundary
    async def touch_session(self, session_id: str) -> None:
        await self._s.execute(
            text("UPDATE public.chat_session SET updated_at = now() WHERE id = :id"),
            {"id": session_id},
        )

    async def list_sessions(
        self, *, user_id: str, tenant_id: str, limit: int, keyword: str = ""
    ) -> list[dict[str, object]]:
        """本人本租户会话列表,updated_at 倒序(id DESC 作时序 tiebreak)。

        keyword 非空时按「标题 OR user 问句(EXISTS)」ILIKE 包含匹配(010 会话搜索);
        ESCAPE '\' + like_pattern 转义使 %/_/\\ 按字面(007 R1-3 同款防线);
        :kw = '' 短路使空白词零行为变更(与 system/datasource 域同构)。
        """
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, title, updated_at FROM public.chat_session s"
                    " WHERE s.user_id = :u AND s.tenant_id = :t"
                    " AND (:kw = '' OR s.title ILIKE :p ESCAPE '\\'"
                    " OR EXISTS (SELECT 1 FROM public.chat_message m"
                    "            WHERE m.session_id = s.id AND m.role = 'USER'"
                    "            AND m.question ILIKE :p ESCAPE '\\'))"
                    " ORDER BY updated_at DESC, id DESC LIMIT :n"
                ),
                {
                    "u": user_id,
                    "t": tenant_id,
                    "kw": keyword,
                    "p": like_pattern(keyword),
                    "n": limit,
                },
            )
        ).mappings().all()
        return [
            {"id": str(r["id"]), "title": str(r["title"]), "updated_at": r["updated_at"]}
            for r in rows
        ]

    @_store_error_boundary
    async def rename_session(
        self, *, session_id: str, user_id: str, tenant_id: str, title: str
    ) -> dict[str, object] | None:
        """重命名(010):归属校验并入 UPDATE 的 WHERE(原子,无先查后改窗口),
        同时刷新 updated_at(与上游 renameSession 同构);未命中返回 None。"""
        row = (
            await self._s.execute(
                text(
                    "UPDATE public.chat_session SET title = :title, updated_at = now()"
                    " WHERE id = :id AND user_id = :u AND tenant_id = :t"
                    " RETURNING id, title, updated_at"
                ),
                {"title": title, "id": session_id, "u": user_id, "t": tenant_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "updated_at": row["updated_at"],
        }

    async def get_session_title(self, session_id: str) -> str | None:
        """标题单读(010 改写守卫用;会话被并发删除返回 None)。"""
        v = (
            await self._s.execute(
                text("SELECT title FROM public.chat_session WHERE id = :id"),
                {"id": session_id},
            )
        ).scalar_one_or_none()
        return None if v is None else str(v)

    async def first_user_question(self, session_id: str) -> str | None:
        """首条 user 问句(010 标题改写素材与守卫基准);无消息返回 None。"""
        v = (
            await self._s.execute(
                text(
                    "SELECT question FROM public.chat_message"
                    " WHERE session_id = :s AND role = 'USER' AND question IS NOT NULL"
                    " ORDER BY created_at ASC, id ASC LIMIT 1"
                ),
                {"s": session_id},
            )
        ).scalar_one_or_none()
        return None if v is None else str(v)

    @_store_error_boundary
    async def update_title_if_current(
        self, *, session_id: str, expected: str, new_title: str
    ) -> int:
        """LLM 标题改写条件回写(010):仅当当前标题仍等于首问截断值时更新——
        人工重命名(含与 LLM 回调的竞态)永不被自动改写覆盖;返回受影响行数。
        不刷新 updated_at:自动改写非用户活动,不应把老会话顶到列表前。"""
        res = cast(
            "CursorResult[object]",  # DML 语句的 execute 结果为 CursorResult(有 rowcount)
            await self._s.execute(
                text(
                    "UPDATE public.chat_session SET title = :new"
                    " WHERE id = :id AND title = :expected"
                ),
                {"new": new_title, "id": session_id, "expected": expected},
            ),
        )
        return res.rowcount

    async def list_messages(self, *, session_id: str) -> list[dict[str, object]]:
        """会话全部消息,创建时间升序(id ASC tiebreak,与补全取数对偶);
        014 增 rating/corrected_sql 反馈状态列(回显用);
        026 增会话 datasource_key(JOIN 会话快照,artifacts 重算兜底用);
        030 增 attachments(附件摘要回显)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT m.id, m.role, m.question, m.sql_text, m.result_json,"
                    " m.summary, m.status, m.rating, m.corrected_sql, m.feedback_comment,"
                    " m.attachments, s.datasource_key"
                    " FROM public.chat_message m"
                    " JOIN public.chat_session s ON s.id = m.session_id"
                    " WHERE m.session_id = :s"
                    " ORDER BY m.created_at ASC, m.id ASC"
                ),
                {"s": session_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    @_store_error_boundary
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

    @_store_error_boundary
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
        attachments: list[dict[str, object]] | None = None,  # 030
    ) -> str:
        mid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.chat_message"
                " (id, session_id, role, question, sql_text, result_json, summary,"
                "  status, attachments)"
                " VALUES (:id, :s, :role, :q, :sql, :rj, :sum, :st,"
                "         CAST(:att AS jsonb))"
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
                "att": json.dumps(attachments or [], ensure_ascii=False),
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

    @_store_error_boundary
    async def commit(self) -> None:
        """事务提交(ChatRepo 端口,013 构造反转);失败转译 ChatStoreError。"""
        await self._s.commit()

    async def rollback(self) -> None:
        """事务回滚(ChatRepo 端口);不转译——容错段内再失败逃逸为既定语义。"""
        await self._s.rollback()


class SuggestionRepository:
    """建议问题缓存读写(039):chat_suggestion 表。

    与 ChatRepository 共享同一请求会话(同事务语义):
    datasource 管理事务内的级联失效(Evictor 绑定)与建议 upsert/commit
    在各自请求里走本类;lock 为事务级咨询锁,commit/rollback 即释放。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def find(self, *, tenant_id: str, datasource_key: str) -> Suggestion | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT tenant_id, datasource_key, questions"
                    " FROM public.chat_suggestion"
                    " WHERE tenant_id=:t AND datasource_key=:k"
                ),
                {"t": tenant_id, "k": datasource_key},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return Suggestion(
            tenant_id=str(row["tenant_id"]),
            datasource_key=str(row["datasource_key"]),
            questions=[str(q) for q in row["questions"]],
        )

    @_store_error_boundary
    async def lock(self, lock_key: str) -> None:
        """事务级咨询锁(039 并发防击穿):同 key 并发首访串行化,
        取锁后复查缓存命中即免重复生成;commit/rollback 自动释放。"""
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key}
        )

    @_store_error_boundary
    async def upsert(self, suggestion: Suggestion) -> None:
        """幂等落库(唯一约束兜底并发双写;覆盖写保持最新生成结果)。"""
        await self._s.execute(
            text(
                "INSERT INTO public.chat_suggestion"
                " (id, tenant_id, datasource_key, questions)"
                " VALUES (:id, :t, :k, CAST(:q AS jsonb))"
                " ON CONFLICT (tenant_id, datasource_key)"
                " DO UPDATE SET questions = EXCLUDED.questions, updated_at = now()"
            ),
            {
                "id": str(uuid7()),
                "t": suggestion.tenant_id,
                "k": suggestion.datasource_key,
                "q": json.dumps(suggestion.questions, ensure_ascii=False),
            },
        )

    @_store_error_boundary
    async def delete_by_datasource(self, datasource_key: str) -> None:
        """按数据源全租户级联失效(039;datasource 管理事务内调用)。"""
        await self._s.execute(
            text("DELETE FROM public.chat_suggestion WHERE datasource_key=:k"),
            {"k": datasource_key},
        )

    @_store_error_boundary
    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()
