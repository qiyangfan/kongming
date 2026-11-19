"""知识覆盖报告的只读仓储:四类空白与 few-shot 概况的聚合输入(零写入零 LLM)。

与 RagRepository(repository.py)共用同一会话与条目投影;单列为窄类,
管理读面不把覆盖查询混入召回仓储。
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.rag.models import (
    COVERAGE_FAILURE_ROW_LIMIT,
    QUERY_HISTORY_CAP,
    FailureSignal,
    HistoryOverview,
    KnowledgeEntry,
)
from kongming.rag.row_mapping import to_entry


class RagCoverageRepository:
    """知识覆盖报告只读仓储(治理面聚合输入;零写入零 LLM)。

    与 RagRepository 共用同一会话与投影规则,单列为窄类以隔离管理读面。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_all_entries(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        visible_team_ids: Sequence[str] = (),
    ) -> list[KnowledgeEntry]:
        """该 (租户, 数据源) 管理读面登记条目(与 fetch_entries 同投影,无分页)。

        可见集合与列表端点同口径:仅公共条目与调用者所在团队条目进入报告
        (管理读面不成为团队条目存在性的旁路)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, datasource_key, schema_name, table_name, table_comment,"
                    " columns_comment, sample_queries, source_type, updated_at,"
                    " COALESCE(alias, '') AS alias"
                    " FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND (team_id IS NULL OR team_id::text = ANY(CAST(:teams AS text[])))"
                    " ORDER BY schema_name, table_name"
                ),
                {"t": tenant_id, "d": datasource_key, "teams": list(visible_team_ids)},
            )
        ).all()
        return [to_entry(r, tenant_id) for r in rows]

    async def failure_signals(
        self, *, tenant_id: str, datasource_key: str, since: datetime
    ) -> list[FailureSignal]:
        """失败信号投影(chat_message 只读跨域,feedback 域同表先例)。

        口径:status ∈ {FAILED, REJECTED, NO_DATA} ∪ rating=DOWN ∪
        corrected_sql 非空;NO_DATA(如实弃答)作为知识未接住的弱信号计入,
        状态分列呈现。窗口 since 之外不收;行数受应用层上限约束
        (COVERAGE_FAILURE_ROW_LIMIT),归并在领域纯函数内。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT m.question, m.status, COALESCE(m.rating, '') AS rating,"
                    " (m.corrected_sql IS NOT NULL) AS has_corrected, m.created_at"
                    " FROM public.chat_message m"
                    " JOIN public.chat_session s ON s.id = m.session_id"
                    " WHERE s.tenant_id = :t AND s.datasource_key = :d"
                    " AND m.role = 'ASSISTANT' AND m.question IS NOT NULL"
                    " AND m.created_at >= CAST(:since AS timestamptz)"
                    " AND (m.status IN ('FAILED', 'REJECTED', 'NO_DATA')"
                    "      OR m.rating = 'DOWN' OR m.corrected_sql IS NOT NULL)"
                    " ORDER BY m.created_at DESC, m.id DESC"
                    " LIMIT :lim"
                ),
                {
                    "t": tenant_id,
                    "d": datasource_key,
                    "since": since,
                    "lim": COVERAGE_FAILURE_ROW_LIMIT,
                },
            )
        ).all()
        return [
            FailureSignal(
                question=str(r.question),
                status=str(r.status),
                rating=str(r.rating),
                has_corrected_sql=bool(r.has_corrected),
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def history_overview(self, *, tenant_id: str, datasource_key: str) -> HistoryOverview:
        """few-shot 通道概况:仅 SUCCESS(召回同口径);计数一次聚合完成。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT COUNT(*) AS n, COUNT(DISTINCT question) AS q"
                    " FROM rag.query_history"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND status = 'SUCCESS'"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).one()
        return HistoryOverview(
            success_count=int(row.n),
            distinct_questions=int(row.q),
            cap=QUERY_HISTORY_CAP,
        )
