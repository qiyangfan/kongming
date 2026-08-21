"""feedback 仓储(014):chat_message 反馈列读写 + 统计/分页 SQL(FeedbackRepo 端口实现)。

纪律:归属判定收口(不存在与越权同形 None);审计复用 auth 域 SqlOperLogRepo
(同会话=同事务,007 组合模式);统计口径 Asia/Shanghai 固定时区分桶(spec)。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.feedback.models import OverviewData


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._oper = SqlOperLogRepo(session)  # 审计与业务同会话=同事务(007 组合模式)

    # ============ 提交面 ============

    async def find_bound_tenant(self, *, user_id: str, tenant_key: str) -> str | None:
        """显式租户白名单复校(与 chat 域逐字同构,008 先例自备)。"""
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
        """缺省取默认租户(与 chat 域逐字同构):default 优先,否则最早授予。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT t.id, t.tenant_key FROM public.sys_tenant t"
                    " JOIN public.sys_user_tenant ut ON ut.tenant_id = t.id"
                    " WHERE ut.user_id = :u AND t.status = 'ACTIVE'"
                    " ORDER BY (t.tenant_key = 'default') DESC, ut.granted_at ASC, t.id"
                    " LIMIT 1"
                ),
                {"u": user_id},
            )
        ).mappings().one_or_none()
        return None if row is None else (str(row["id"]), str(row["tenant_key"]))

    async def find_message_for_feedback(
        self, *, message_id: str, user_id: str, tenant_id: str
    ) -> dict[str, object] | None:
        """归属收口:消息 JOIN 会话 JOIN 租户,user_id+tenant_id 双匹配(同形 None);
        顺带取防火墙 schema 边界(default 源=tenant_<key>,动态源=default_schema)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT m.id, m.role, m.session_id, m.created_at,"
                    " s.datasource_key, m.corrected_sql, t.tenant_key,"
                    " coalesce(ds.default_schema, 'tenant_' || t.tenant_key) AS scope_schema"
                    " FROM public.chat_message m"
                    " JOIN public.chat_session s ON s.id = m.session_id"
                    " JOIN public.sys_tenant t ON t.id = s.tenant_id"
                    " LEFT JOIN public.sys_datasource ds"
                    "   ON ds.datasource_key = s.datasource_key AND ds.tenant_id = s.tenant_id"
                    " WHERE m.id = :mid AND s.user_id = :u AND s.tenant_id = :t"
                ),
                {"mid": message_id, "u": user_id, "t": tenant_id},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    async def lock_feedback_row(self, *, message_id: str) -> dict[str, object] | None:
        """FOR UPDATE 读现反馈值:并发提交同一消息行锁串行(spec 幂等覆盖)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT rating, feedback_comment, corrected_sql"
                    " FROM public.chat_message WHERE id = :mid FOR UPDATE"
                ),
                {"mid": message_id},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    async def submit_feedback(
        self,
        *,
        message_id: str,
        rating: str,
        comment: str | None,
        corrected_sql: str | None,
    ) -> None:
        """覆盖写反馈列;comment/corrected_sql 传 service 三态合并后的最终值。
        重复提交=重新待处置(R1-O2):同步清空处置三列(handler/handle_time/
        handle_note),防「新 PENDING + 陈旧处置者」组合。"""
        await self._s.execute(
            text(
                "UPDATE public.chat_message"
                " SET rating = :r, feedback_comment = :c, corrected_sql = :cs,"
                " feedback_time = now(), feedback_status = 'PENDING',"
                " feedback_handler = NULL, feedback_handle_time = NULL,"
                " feedback_handle_note = NULL"
                " WHERE id = :mid"
            ),
            {"r": rating, "c": comment, "cs": corrected_sql, "mid": message_id},
        )

    async def recent_user_question_before(
        self, *, session_id: str, message_created_at: object
    ) -> str | None:
        """同会话该消息之前最近一条 user 问句(created_at 严格早于,id DESC tiebreak)。"""
        v = (
            await self._s.execute(
                text(
                    "SELECT question FROM public.chat_message"
                    " WHERE session_id = :sid AND role = 'USER' AND question IS NOT NULL"
                    " AND created_at < :ts"
                    " ORDER BY created_at DESC, id DESC LIMIT 1"
                ),
                {"sid": session_id, "ts": message_created_at},
            )
        ).scalar_one_or_none()
        return None if v is None else str(v)

    # ============ 管理面 ============

    async def user_has_scope_all(self, *, user_id: str) -> bool:
        """角色集含 data_scope=ALL 即 True(无角色 None→False,降级收紧)。"""
        v = (
            await self._s.execute(
                text(
                    "SELECT bool_or(r.data_scope = 'ALL') FROM public.sys_role r"
                    " JOIN public.sys_user_role ur ON ur.role_id = r.id"
                    " WHERE ur.user_id = :u"
                ),
                {"u": user_id},
            )
        ).scalar_one_or_none()
        return bool(v)

    async def find_message_for_admin(
        self, *, message_id: str, vis_user_id: str | None, require_feedback: bool = False
    ) -> dict[str, object] | None:
        """处置/涉及表归属(vis 非 None 强制本人会话);返回消息全要素。
        require_feedback=True(处置路径,R1-O1)限定已反馈的 assistant 消息,
        防对任意消息处置产生「状态非 NONE 但 feedback_time 空」的幽灵行;
        涉及表定位维持宽松(零反馈消息的 SQL 也可定位)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT m.id, m.session_id, m.role, m.sql_text, m.corrected_sql,"
                    " m.rating, m.feedback_comment, m.feedback_status,"
                    " m.feedback_handle_note, m.feedback_time, m.created_at,"
                    " s.user_id, s.tenant_id, s.datasource_key"
                    " FROM public.chat_message m"
                    " JOIN public.chat_session s ON s.id = m.session_id"
                    " WHERE m.id = :mid"
                    " AND (CAST(:vis AS uuid) IS NULL OR s.user_id = CAST(:vis AS uuid))"
                    " AND (NOT :rf OR (m.role = 'ASSISTANT' AND m.feedback_time IS NOT NULL))"
                ),
                {"mid": message_id, "vis": vis_user_id, "rf": require_feedback},
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    async def handle_status(
        self, *, message_id: str, status: str, note: str | None, handler_id: str
    ) -> bool:
        """处置落库;note 传 service 三态合并后的最终值;未命中 False。"""
        res = await self._s.execute(
            text(
                "UPDATE public.chat_message"
                " SET feedback_status = :st, feedback_handler = :h,"
                " feedback_handle_time = now(), feedback_handle_note = :note"
                " WHERE id = :mid RETURNING id"
            ),
            {"st": status, "h": handler_id, "note": note, "mid": message_id},
        )
        return res.scalar_one_or_none() is not None

    # ============ 统计/分页 ============

    _PAGE_SELECT = (
        "SELECT m.id AS message_id, m.session_id, m.rating, m.feedback_comment,"
        " m.corrected_sql, m.feedback_status, m.feedback_time, m.sql_text,"
        " s.user_id, u.nickname AS user_nickname,"
        " s.tenant_id, t.tenant_key, s.datasource_key,"
        " (SELECT q.question FROM public.chat_message q"
        "  WHERE q.session_id = m.session_id AND q.role = 'USER'"
        "   AND q.question IS NOT NULL AND q.created_at < m.created_at"
        "  ORDER BY q.created_at DESC, q.id DESC LIMIT 1) AS question"
        " FROM public.chat_message m"
        " JOIN public.chat_session s ON s.id = m.session_id"
        " JOIN public.sys_user u ON u.id = s.user_id"
        " JOIN public.sys_tenant t ON t.id = s.tenant_id"
    )
    _PAGE_WHERE = (
        " WHERE m.role = 'ASSISTANT' AND m.feedback_time IS NOT NULL"
        " AND (CAST(:vis AS uuid) IS NULL OR s.user_id = CAST(:vis AS uuid))"
        " AND (CAST(:uid AS uuid) IS NULL OR s.user_id = CAST(:uid AS uuid))"
        " AND (CAST(:rating AS text) IS NULL OR m.rating = CAST(:rating AS text))"
        " AND (CAST(:tid AS uuid) IS NULL OR s.tenant_id = CAST(:tid AS uuid))"
        " AND (CAST(:ds AS text) IS NULL OR s.datasource_key = CAST(:ds AS text))"
        " AND (CAST(:fst AS text) IS NULL OR m.feedback_status = CAST(:fst AS text))"
        " AND (NOT :oc OR (m.corrected_sql IS NOT NULL AND m.corrected_sql <> ''))"
        " AND (CAST(:st AS timestamptz) IS NULL OR m.feedback_time >= CAST(:st AS timestamptz))"
        " AND (CAST(:et AS timestamptz) IS NULL OR m.feedback_time < CAST(:et AS timestamptz))"
    )

    async def page_feedback(
        self,
        *,
        vis_user_id: str | None,
        user_id: str | None,
        rating: str | None,
        tenant_id: str | None,
        datasource_key: str | None,
        feedback_status: str | None,
        only_corrected: bool,
        start_time: object | None,
        end_time: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        params = {
            "vis": vis_user_id,
            "uid": user_id,
            "rating": rating,
            "tid": tenant_id,
            "ds": datasource_key,
            "fst": feedback_status,
            "oc": only_corrected,
            "st": start_time,
            "et": end_time,
            "lim": size,
            "off": (page - 1) * size,
        }
        total = int(
            (
                await self._s.execute(
                    text("SELECT count(*) FROM public.chat_message m"  # noqa: S608  # 常量模板,参数化绑定
                         " JOIN public.chat_session s ON s.id = m.session_id"
                         " JOIN public.sys_user u ON u.id = s.user_id"
                         " JOIN public.sys_tenant t ON t.id = s.tenant_id" + self._PAGE_WHERE),
                    params,
                )
            ).scalar_one()
        )
        rows = (
            await self._s.execute(
                text(
                    self._PAGE_SELECT + self._PAGE_WHERE  # noqa: S608  # 常量模板,参数化绑定
                    + " ORDER BY m.feedback_time DESC, m.id DESC LIMIT :lim OFFSET :off"
                ),
                params,
            )
        ).mappings().all()
        return total, [dict(r) for r in rows]

    async def overview_feedback(
        self,
        *,
        vis_user_id: str | None,
        days: int,
        tenant_id: str | None,
        datasource_key: str | None,
    ) -> OverviewData:
        """汇总+trend;时区固定 Asia/Shanghai 分桶,trend generate_series 逐天补零。"""
        base_where = (
            " WHERE m.role = 'ASSISTANT' AND m.feedback_time IS NOT NULL"
            " AND m.feedback_time >= now() - make_interval(days => :days)"
            " AND (CAST(:vis AS uuid) IS NULL OR s.user_id = CAST(:vis AS uuid))"
            " AND (CAST(:tid AS uuid) IS NULL OR s.tenant_id = CAST(:tid AS uuid))"
            " AND (CAST(:ds AS text) IS NULL OR s.datasource_key = CAST(:ds AS text))"
        )
        params = {"vis": vis_user_id, "tid": tenant_id, "ds": datasource_key, "days": days}
        agg = (
            await self._s.execute(
                text(
                    "SELECT count(*) AS total,"  # noqa: S608  # 常量模板,参数化绑定
                    " count(*) FILTER (WHERE m.rating = 'UP') AS up_count,"
                    " count(*) FILTER (WHERE m.rating = 'DOWN') AS down_count,"
                    " count(*) FILTER (WHERE m.corrected_sql IS NOT NULL"
                    "   AND m.corrected_sql <> '') AS corrected_count"
                    " FROM public.chat_message m"
                    " JOIN public.chat_session s ON s.id = m.session_id" + base_where
                ),
                params,
            )
        ).mappings().one()
        trend_rows = (
            await self._s.execute(
                text(
                    "SELECT to_char(d.day, 'YYYY-MM-DD') AS day,"
                    " count(m.id) FILTER (WHERE m.rating = 'UP') AS up,"
                    " count(m.id) FILTER (WHERE m.rating = 'DOWN') AS down"
                    " FROM generate_series("
                    "   (now() AT TIME ZONE 'Asia/Shanghai')::date - (:days - 1),"
                    "   (now() AT TIME ZONE 'Asia/Shanghai')::date, interval '1 day') AS d(day)"
                    " LEFT JOIN (public.chat_message m JOIN public.chat_session s"
                    "   ON s.id = m.session_id)"
                    "   ON m.role = 'ASSISTANT' AND m.feedback_time IS NOT NULL"
                    "  AND (m.feedback_time AT TIME ZONE 'Asia/Shanghai')::date = d.day"
                    "  AND (CAST(:vis AS uuid) IS NULL OR s.user_id = CAST(:vis AS uuid))"
                    "  AND (CAST(:tid AS uuid) IS NULL OR s.tenant_id = CAST(:tid AS uuid))"
                    "  AND (CAST(:ds AS text) IS NULL OR s.datasource_key = CAST(:ds AS text))"
                    " GROUP BY d.day ORDER BY d.day"
                ),
                params,
            )
        ).mappings().all()
        total = int(agg["total"])
        down = int(agg["down_count"])
        return {
            "total": total,
            "upCount": int(agg["up_count"]),
            "downCount": down,
            "correctedCount": int(agg["corrected_count"]),
            "downRate": (down / total) if total else 0.0,
            "trend": [
                {"day": str(r["day"]), "up": int(r["up"]), "down": int(r["down"])}
                for r in trend_rows
            ],
        }

    async def match_knowledge(
        self, *, tenant_id: str, datasource_key: str, table_names: list[str]
    ) -> list[dict[str, object]]:
        """按 tenant+datasource+table_name 匹配知识库条目(涉及表定位)。"""
        if not table_names:
            return []
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, schema_name, table_name FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :ds"
                    " AND table_name = ANY(CAST(:names AS text[]))"
                ),
                {"t": tenant_id, "ds": datasource_key, "names": table_names},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    # ============ 审计/事务 ============

    async def log_oper(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None:
        await self._oper.add(user_id, action, target, detail)

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()
