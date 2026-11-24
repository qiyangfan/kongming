"""问答质量信号聚合查询:轮次按 assistant 消息统计;token 按 trace_id 汇总
再平均(失败调用并入);趋势按上海时区自然日分桶补零;点踩率以全部轮次为分母。

轮次范围单源在 _ROUNDS:已取消的轮没有可评价的答案,汇总、趋势、明细、同会话串联
与导出一律不计(observability spec「已取消轮次的观测口径」);按消息标识取的
节点轨迹不在此列,已取消轮仍可追溯。"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ROUNDS = (
    "(SELECT m.* FROM public.chat_message m WHERE m.role = 'ASSISTANT' AND m.status <> 'CANCELLED')"
)


class SqlQualityRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def overview(
        self,
        *,
        tenant_ids: list[str] | None,
        start: object | None,
        end: object | None,
        low_score_threshold: int,
    ) -> dict[str, Any]:
        where, params = _scope(tenant_ids, start, end)
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT"  # noqa: S608
                        " count(*) AS total,"
                        " count(*) FILTER (WHERE m.status = 'SUCCESS') AS success,"
                        " count(*) FILTER (WHERE m.status = 'NO_DATA') AS no_data,"
                        " count(*) FILTER (WHERE m.status = 'REJECTED') AS blocked,"
                        " count(*) FILTER (WHERE m.status = 'FAILED') AS failed,"
                        " count(*) FILTER (WHERE m.retry_count > 0) AS selfheal,"
                        " count(*) FILTER (WHERE m.rating = 'DOWN') AS down,"
                        " count(*) FILTER (WHERE m.fallback_type = 'CHITCHAT') AS chitchat,"
                        " count(*) FILTER (WHERE m.fallback_type = 'CLARIFY') AS clarify,"
                        " round(avg(sc.score)::numeric, 1) AS avg_score,"
                        " count(*) FILTER (WHERE sc.score < :low) AS low_score_count"
                        f" FROM {_ROUNDS} m"
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        " LEFT JOIN public.chat_turn_score sc ON sc.message_id = m.id"
                        f" WHERE {where}"
                    ),
                    {**params, "low": low_score_threshold},
                )
            )
            .mappings()
            .one()
        )
        out = dict(row)
        # 平均 token:按 trace_id 分组的轮次 SUM 再平均(口径;trace_id
        # 缺失的旧行不参与 token 均值——无关联用量可归)
        avg = (
            await self._s.execute(
                text(
                    "SELECT avg(turn_tokens) AS avg_tokens FROM ("  # noqa: S608
                    " SELECT sum(u.total_tokens) AS turn_tokens"
                    f" FROM {_ROUNDS} m"
                    " JOIN public.chat_session s ON s.id = m.session_id"
                    " JOIN public.sys_llm_usage u ON u.trace_id = m.trace_id"
                    " WHERE m.trace_id IS NOT NULL"
                    f" AND {where}"
                    " GROUP BY m.trace_id)"
                ),
                params,
            )
        ).scalar()
        out["avg_tokens"] = float(avg) if avg is not None else 0.0
        return out

    async def daily_trend(self, *, tenant_ids: list[str] | None, days: int) -> list[dict[str, Any]]:
        tenant_filter, params = _tenant_filter(tenant_ids)
        rows = (
            (
                await self._s.execute(
                    text(
                        "WITH bounds AS ("  # noqa: S608
                        "  SELECT (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')"
                        "          ::date - (:days - 1)) AS start_d"
                        "), day_series AS ("
                        "  SELECT generate_series(b.start_d,"
                        "    date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')::date,"
                        "    '1 day')::date AS d"
                        "  FROM bounds b"
                        ")"
                        "SELECT to_char(ds.d, 'YYYY-MM-DD') AS date,"
                        "       count(m.id) AS rounds,"
                        "       count(m.id) FILTER (WHERE m.status = 'SUCCESS') AS success,"
                        "       count(m.id) FILTER (WHERE m.status = 'NO_DATA') AS no_data,"
                        "       count(m.id) FILTER (WHERE m.status = 'REJECTED') AS blocked,"
                        "       count(m.id) FILTER (WHERE m.status = 'FAILED') AS failed"
                        " FROM day_series ds"
                        f" LEFT JOIN {_ROUNDS} m"
                        "   ON date_trunc('day', m.created_at AT TIME ZONE 'Asia/Shanghai')"
                        "       ::date = ds.d"
                        "  AND m.session_id IN ("
                        f"    SELECT id FROM public.chat_session WHERE {tenant_filter})"
                        " GROUP BY ds.d ORDER BY ds.d"
                    ),
                    {**params, "days": days},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def by_datasource(
        self, *, tenant_ids: list[str] | None, start: object | None, end: object | None
    ) -> list[dict[str, Any]]:
        where, params = _scope(tenant_ids, start, end)
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT s.datasource_key AS datasource_key,"  # noqa: S608
                        "       count(*) AS rounds,"
                        "       count(*) FILTER (WHERE m.status = 'SUCCESS') AS success,"
                        "       count(*) FILTER (WHERE m.status = 'NO_DATA') AS no_data,"
                        "       count(*) FILTER (WHERE m.status = 'REJECTED') AS blocked,"
                        "       count(*) FILTER (WHERE m.status = 'FAILED') AS failed"
                        f" FROM {_ROUNDS} m"
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        f" WHERE {where}"
                        " GROUP BY s.datasource_key ORDER BY rounds DESC"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def by_prompt_version(
        self, *, tenant_ids: list[str] | None, start: object | None, end: object | None
    ) -> list[dict[str, Any]]:
        where, params = _scope(tenant_ids, start, end)
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT COALESCE(m.prompt_version, '-') AS prompt_version,"  # noqa: S608
                        "       count(*) AS rounds,"
                        "       count(*) FILTER (WHERE m.status = 'SUCCESS') AS success,"
                        "       count(*) FILTER (WHERE m.status = 'NO_DATA') AS no_data,"
                        "       count(*) FILTER (WHERE m.status = 'REJECTED') AS blocked,"
                        "       count(*) FILTER (WHERE m.status = 'FAILED') AS failed"
                        f" FROM {_ROUNDS} m"
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        f" WHERE {where}"
                        " GROUP BY m.prompt_version ORDER BY rounds DESC"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def page(
        self,
        *,
        tenant_ids: list[str] | None,
        status: str | None,
        datasource_key: str | None,
        fallback: str | None,
        start: object | None,
        end: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        where, params = _scope(tenant_ids, start, end)
        conds: list[str] = []
        if status:
            conds.append("m.status = :st")
            params = {**params, "st": status}
        if datasource_key:
            conds.append("s.datasource_key = :dk")
            params = {**params, "dk": datasource_key}
        if fallback == "none":
            conds.append("m.fallback_type IS NULL")
        elif fallback in ("CHITCHAT", "CLARIFY"):
            conds.append("m.fallback_type = :fb")
            params = {**params, "fb": fallback}
        extra = f" AND {' AND '.join(conds)}" if conds else ""
        total = int(
            (
                await self._s.execute(
                    text(
                        f"SELECT count(*) FROM {_ROUNDS} m"  # noqa: S608
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        f" WHERE {where}{extra}"
                    ),
                    params,
                )
            ).scalar()
            or 0
        )
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT m.id, m.created_at, m.question, m.resolved_question,"  # noqa: S608
                        "       m.status, m.retry_count, m.rating, m.trace_id,"
                        "       m.prompt_version, m.fallback_type, m.session_id,"
                        "       s.datasource_key AS datasource_key,"
                        "       sc.score, sc.grounding, sc.coverage"
                        f" FROM {_ROUNDS} m"
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        " LEFT JOIN public.chat_turn_score sc ON sc.message_id = m.id"
                        f" WHERE {where}{extra}"
                        " ORDER BY m.created_at DESC LIMIT :lim OFFSET :off"
                    ),
                    {**params, "lim": size, "off": (page - 1) * size},
                )
            )
            .mappings()
            .all()
        )
        return total, [dict(r) for r in rows]

    async def score_detail(
        self, *, tenant_ids: list[str] | None, message_id: str
    ) -> dict[str, Any] | None:
        """评分详情(按消息取;租户不可见/未评分/非 assistant 轮 → None)。"""
        conds = ["sc.message_id = :mid", "m.role = 'ASSISTANT'"]
        params: dict[str, object] = {"mid": message_id}
        if tenant_ids is not None:
            conds.append("s.tenant_id = ANY(CAST(:t_ids AS uuid[]))")
            params["t_ids"] = tenant_ids
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT sc.message_id, sc.score, sc.grounding, sc.coverage,"  # noqa: S608
                        "       sc.decomposition, sc.score_model, sc.scored_at"
                        " FROM public.chat_turn_score sc"
                        " JOIN public.chat_message m ON m.id = sc.message_id"
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        f" WHERE {' AND '.join(conds)}"
                    ),
                    params,
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    async def session_context(
        self, *, tenant_ids: list[str] | None, message_id: str, limit: int
    ) -> dict[str, Any] | None:
        """同会话轮次串联(spec「同会话轮次串联」):锚点轮必含,窗口取锚点
        及此前最近 limit 轮(升序返回);租户不可见/不存在 → None(404 同形
        不区分存在性)。id(UUID v7)作时序 tiebreak。"""
        anchor_conds = ["m.id = :mid"]
        params: dict[str, object] = {"mid": message_id, "lim": limit}
        if tenant_ids is not None:
            anchor_conds.append("s.tenant_id = ANY(CAST(:t_ids AS uuid[]))")
            params["t_ids"] = tenant_ids
        anchor = (
            await self._s.execute(
                text(
                    f"SELECT m.session_id FROM {_ROUNDS} m"  # noqa: S608
                    " JOIN public.chat_session s ON s.id = m.session_id"
                    f" WHERE {' AND '.join(anchor_conds)}"
                ),
                params,
            )
        ).scalar()
        if anchor is None:
            return None
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT m.id, m.question, m.status, m.fallback_type,"  # noqa: S608
                        "       sc.score, m.created_at"
                        f" FROM {_ROUNDS} m"
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        " LEFT JOIN public.chat_turn_score sc ON sc.message_id = m.id"
                        " WHERE m.session_id = :sid"
                        "   AND (m.created_at, m.id) <="
                        "       (SELECT m2.created_at, m2.id FROM public.chat_message m2"
                        "         WHERE m2.id = :mid)"
                        " ORDER BY m.created_at DESC, m.id DESC LIMIT :lim"
                    ),
                    {**params, "sid": str(anchor)},
                )
            )
            .mappings()
            .all()
        )
        return {
            "session_id": str(anchor),
            "items": [dict(r) for r in reversed(rows)],  # 时间升序
        }

    async def stage_trace_detail(
        self, *, tenant_ids: list[str] | None, message_id: str
    ) -> dict[str, Any] | None:
        """节点阶段轨迹(按消息取;租户不可见/非 assistant 轮 → None;
        同步轮/无帧流轮为 null=如实为空)。"""
        conds = ["m.id = :mid", "m.role = 'ASSISTANT'"]
        params: dict[str, object] = {"mid": message_id}
        if tenant_ids is not None:
            conds.append("s.tenant_id = ANY(CAST(:t_ids AS uuid[]))")
            params["t_ids"] = tenant_ids
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT m.stage_trace FROM public.chat_message m"  # noqa: S608
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        f" WHERE {' AND '.join(conds)}"
                    ),
                    params,
                )
            )
            .mappings()
            .one_or_none()
        )
        # 行不存在/租户不可见 → None(路由映射 404,与 score/context 同形防探测);
        # 行在而轨迹为空 → trace 键为 null(同步轮如实为空,两者不得混同)
        return {"stage_trace": row["stage_trace"]} if row is not None else None

    async def export_rows(
        self,
        *,
        tenant_ids: list[str] | None,
        status: str | None,
        datasource_key: str | None,
        fallback: str | None,
        start: object | None,
        end: object | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """导出行集(page 同筛选,无分页,行数上限在服务层收口)。"""
        where, params = _scope(tenant_ids, start, end)
        conds: list[str] = []
        if status:
            conds.append("m.status = :st")
            params = {**params, "st": status}
        if datasource_key:
            conds.append("s.datasource_key = :dk")
            params = {**params, "dk": datasource_key}
        if fallback == "none":
            conds.append("m.fallback_type IS NULL")
        elif fallback in ("CHITCHAT", "CLARIFY"):
            conds.append("m.fallback_type = :fb")
            params = {**params, "fb": fallback}
        extra = f" AND {' AND '.join(conds)}" if conds else ""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT m.created_at, m.question, m.resolved_question,"  # noqa: S608
                        "       m.status, m.fallback_type, m.retry_count, m.rating,"
                        "       sc.score, sc.grounding, sc.coverage, m.trace_id,"
                        "       m.session_id, s.datasource_key, m.prompt_version"
                        f" FROM {_ROUNDS} m"
                        " JOIN public.chat_session s ON s.id = m.session_id"
                        " LEFT JOIN public.chat_turn_score sc ON sc.message_id = m.id"
                        f" WHERE {where}{extra}"
                        " ORDER BY m.created_at DESC, m.id DESC LIMIT :lim"
                    ),
                    {**params, "lim": limit},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_bound_tenant_ids(self, *, user_id: str) -> list[str]:
        """查询方绑定租户全集(ACTIVE;非 ALL 权限方的收敛源——观测面越权
        可见性等同数据越权,audit 同款口径)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT t.id FROM public.sys_tenant t"
                    " JOIN public.sys_user_tenant ut ON ut.tenant_id = t.id"
                    " WHERE ut.user_id = :u AND t.status = 'ACTIVE'"
                ),
                {"u": user_id},
            )
        ).all()
        return [str(r[0]) for r in rows]

    async def user_has_scope_all(self, *, user_id: str) -> bool:
        """data_scope=ALL 判定(audit 域同款查询:角色经 sys_user_role 关联,
        bool_or 覆盖多角色并集;多角色时任一 ALL 即全域可见)。

        与 audit/feedback/toolcall 同一道启用闸:非启用角色的 ALL 不兑现——
        本判定直接决定跨租户全量可见,漏闸等于留一条「禁用角色反而放宽观测面」的暗道。
        """
        row = (
            await self._s.execute(
                text(
                    "SELECT bool_or(r.data_scope = 'ALL')"
                    " FROM public.sys_role r"
                    " JOIN public.sys_user_role ur ON ur.role_id = r.id"
                    " WHERE ur.user_id = :u AND r.status = 'ACTIVE'"
                ),
                {"u": user_id},
            )
        ).scalar_one_or_none()
        return bool(row)


def _tenant_filter(
    tenant_ids: list[str] | None,
) -> tuple[str, dict[str, object]]:
    """租户维谓词(不含 role/时间):None=不限;空列表=无可见租户。
    注意:租户维挂 chat_session.tenant_id,消息维查询须 JOIN session(调用方)。"""
    if tenant_ids is None:
        return "TRUE", {}
    return "tenant_id = ANY(CAST(:t_ids AS uuid[]))", {"t_ids": tenant_ids}


def _scope(
    tenant_ids: list[str] | None, start: object | None, end: object | None
) -> tuple[str, dict[str, object]]:
    """轮次的租户收敛与时间窗谓词(与 _ROUNDS 搭配使用);start 缺省=不限下界
    (补零桶不受损)。
    tenant_ids=None 不限租户(ALL 权限方无显式过滤);空列表=无可见租户(收敛
    后无绑定,产出空集而非全量)。"""
    conds: list[str] = []
    params: dict[str, object] = {}
    if tenant_ids is not None:
        conds.append("s.tenant_id = ANY(CAST(:t_ids AS uuid[]))")
        params["t_ids"] = tenant_ids
    if start is not None:
        conds.append("m.created_at >= :st")
        params["st"] = start
    if end is not None:
        conds.append("m.created_at < :en")
        params["en"] = end
    return " AND ".join(conds) or "TRUE", params
