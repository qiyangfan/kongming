"""chat 会话管理用例:列表、重命名、消息历史、删除与结果导出。

与问答管线分离:本服务只消费会话/消息归属校验与导出审计,不触 Agent 面;
租户解析与问答管线共用 service 模块级 resolve_tenant。
"""

import contextlib
import logging

from kongming.chat.domain_errors import InvalidTitle, SessionNotFound
from kongming.chat.export import to_csv, to_xlsx
from kongming.chat.message_view import row_to_message
from kongming.chat.ports import ChatRepo, OperLogger
from kongming.chat.results import SessionMessage, SessionSummary
from kongming.chat.service import resolve_tenant

log = logging.getLogger(__name__)

SESSION_TITLE_MAX_LEN = 200  # 标题列上限(chat spec「会话重命名」;strip 后判定)


class ChatSessionService:
    def __init__(self, repo: ChatRepo, *, oper_logger: OperLogger | None = None) -> None:
        self._repo = repo
        self._oper_logger = oper_logger

    async def list_sessions(
        self, *, user_id: str, tenant_key: str | None, limit: int, keyword: str = ""
    ) -> list[SessionSummary]:
        tenant_id, _ = await resolve_tenant(self._repo, user_id, tenant_key)
        rows = await self._repo.list_sessions(
            user_id=user_id, tenant_id=tenant_id, limit=limit, keyword=keyword.strip()
        )
        return [
            SessionSummary(id=str(r["id"]), title=str(r["title"]), updated_at=r["updated_at"])
            for r in rows
        ]

    async def rename_session(
        self, *, user_id: str, tenant_key: str | None, session_id: str, title: str
    ) -> SessionSummary:
        """重命名:标题去空白后判空与列上限;归属原子 UPDATE。成功后该标题
        即人工设定,不再被 LLM 自动改写覆盖(改写走 expected 条件更新,
        见 title_tasks)。"""
        tenant_id, _ = await resolve_tenant(self._repo, user_id, tenant_key)
        cleaned = title.strip()
        if not cleaned or len(cleaned) > SESSION_TITLE_MAX_LEN:
            raise InvalidTitle(title)
        row = await self._repo.rename_session(
            session_id=session_id, user_id=user_id, tenant_id=tenant_id, title=cleaned
        )
        if row is None:
            raise SessionNotFound(session_id)
        await self._repo.commit()
        return SessionSummary(
            id=str(row["id"]), title=str(row["title"]), updated_at=row["updated_at"]
        )

    async def session_messages(
        self, *, user_id: str, tenant_key: str | None, session_id: str
    ) -> list[SessionMessage]:
        tenant_id, _ = await resolve_tenant(self._repo, user_id, tenant_key)
        sid = await self._repo.find_session_owned(
            session_id=session_id, user_id=user_id, tenant_id=tenant_id
        )
        if sid is None:
            raise SessionNotFound(session_id)
        rows = await self._repo.list_messages(session_id=sid)
        return [row_to_message(r) for r in rows]

    async def delete_session(
        self, *, user_id: str, tenant_key: str | None, session_id: str
    ) -> None:
        tenant_id, _ = await resolve_tenant(self._repo, user_id, tenant_key)
        deleted = await self._repo.delete_session(
            session_id=session_id, user_id=user_id, tenant_id=tenant_id
        )
        if not deleted:
            raise SessionNotFound(session_id)
        await self._repo.commit()

    async def export_result(
        self,
        *,
        user_id: str,
        tenant_key: str | None,
        fmt: str,
        columns: list[str],
        rows: list[list[object]],
        max_rows: int,
    ) -> tuple[bytes, str, str]:
        """所见即所导(不重跑 SQL):仅做租户白名单复校 + 格式转换;
        返回 (内容字节, media_type, 扩展名)。截断仅 warn,不报错(chat spec)。"""
        await resolve_tenant(self._repo, user_id, tenant_key)
        if fmt == "csv":
            content, truncated = to_csv(columns, rows, max_rows=max_rows)
            media_type, ext = "text/csv; charset=utf-8", "csv"
        else:
            content, truncated = to_xlsx(columns, rows, max_rows=max_rows)
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        if truncated:
            log.warning("导出行数截断: %d → %d 行", len(rows), max_rows)
        # 导出审计:数据外发可见性(audit spec 铁律,与 SqlAuditWriter 同语义);
        # 写失败不阻断导出,rollback 也包保护——连接级双失败时异常不逃逸
        # (裸 rollback 再抛会把导出变 500)
        if self._oper_logger is not None:
            try:
                await self._oper_logger.add(
                    user_id,
                    "chat.export",
                    None,
                    {"fmt": fmt, "rows": len(rows), "truncated": truncated},
                )
                await self._repo.commit()
            except Exception:
                log.warning("导出审计落库失败(不阻断)", exc_info=True)
                with contextlib.suppress(Exception):
                    await self._repo.rollback()
        return content, media_type, ext
