"""quality 仓储(069):chat_message 质量信号聚合 SQL。

口径:
- 轮次 = role='ASSISTANT' 的 chat_message(USER 行是问句,非质量轮次);
- 平均 token(072 任务 3.3 口径核对):同 trace_id 下「1 次 ERROR(tokens=0)
  + 1 次 SUCCESS」并存时按行平均会被 0 值稀释——必须按 trace_id 分组
  SUM(total_tokens) 得轮次总量再平均(尝试次数另计);
- 趋势:Asia/Shanghai 日历日分桶逐天补零(窗口边界与分桶同口径,范围谓词
  放 ON 内——WHERE 会把补零桶过滤掉,月聚合时区教训先例);
- 数据源切片经 chat_session.datasource_key(消息行不背维度);
- 点踩率:rating='DOWN' / 全部轮次(与 feedback 域 downRate 同口径——
  "已评轮次才计入分母"的说法与两域实际实现都不符,rated 计数仅随行产出
  备查,不参与任何比率)。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ROUNDS = "(SELECT m.* FROM public.chat_message m WHERE m.role = 'ASSISTANT')"


class SqlQualityRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def overview(
        self,
        *,
        tenant_ids: list[str] | None,
        start: object | None,
        end: object | None,
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
                        " count(*) FILTER (WHERE m.rating = 'DOWN') AS down"
                        f" FROM {_ROUNDS} m"  # noqa: S608
                        " JOIN public.chat_session s ON s.id = m.session_id"  # noqa: S608
                        f" WHERE {where}"  # noqa: S608
                    ),
                    params,
                )
            )
            .mappings()
            .one()
        )
        out = dict(row)
        # 平均 token:按 trace_id 分组的轮次 SUM 再平均(072 口径;trace_id
        # 缺失的旧行不参与 token 均值——无关联用量可归)
        avg = (
            await self._s.execute(
                text(
                    "SELECT avg(turn_tokens) AS avg_tokens FROM ("  # noqa: S608
                    " SELECT sum(u.total_tokens) AS turn_tokens"  # noqa: S608
                    " FROM public.chat_message m"  # noqa: S608
                    " JOIN public.chat_session s ON s.id = m.session_id"  # noqa: S608
                    " JOIN public.sys_llm_usage u ON u.trace_id = m.trace_id"  # noqa: S608
                    " WHERE m.role = 'ASSISTANT' AND m.trace_id IS NOT NULL"  # noqa: S608
                    f" AND {where}"  # noqa: S608
                    " GROUP BY m.trace_id)"  # noqa: S608
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
                        "SELECT to_char(ds.d, 'YYYY-MM-DD') AS date,"  # noqa: S608
                        "       count(m.id) AS rounds,"
                        "       count(m.id) FILTER (WHERE m.status = 'SUCCESS') AS success,"
                        "       count(m.id) FILTER (WHERE m.status = 'NO_DATA') AS no_data,"
                        "       count(m.id) FILTER (WHERE m.status = 'REJECTED') AS blocked,"
                        "       count(m.id) FILTER (WHERE m.status = 'FAILED') AS failed"
                        " FROM day_series ds"  # noqa: S608
                        " LEFT JOIN public.chat_message m"  # noqa: S608
                        "   ON m.role = 'ASSISTANT'"
                        "   AND date_trunc('day', m.created_at AT TIME ZONE 'Asia/Shanghai')"
                        "       ::date = ds.d"
                        "  AND m.session_id IN ("
                        f"    SELECT id FROM public.chat_session WHERE {tenant_filter})"
                        " GROUP BY ds.d ORDER BY ds.d"  # noqa: S608
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
                        f" FROM {_ROUNDS} m"  # noqa: S608
                        " JOIN public.chat_session s ON s.id = m.session_id"  # noqa: S608
                        f" WHERE {where}"  # noqa: S608
                        " GROUP BY s.datasource_key ORDER BY rounds DESC"  # noqa: S608
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
                        f" FROM {_ROUNDS} m"  # noqa: S608
                        " JOIN public.chat_session s ON s.id = m.session_id"  # noqa: S608
                        f" WHERE {where}"  # noqa: S608
                        " GROUP BY m.prompt_version ORDER BY rounds DESC"  # noqa: S608
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
        extra = f" AND {' AND '.join(conds)}" if conds else ""
        total = int(
            (
                await self._s.execute(
                    text(
                        f"SELECT count(*) FROM {_ROUNDS} m"  # noqa: S608
                        " JOIN public.chat_session s ON s.id = m.session_id"  # noqa: S608
                        f" WHERE {where}{extra}"  # noqa: S608
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
                        "       m.prompt_version, s.datasource_key AS datasource_key"
                        f" FROM {_ROUNDS} m"  # noqa: S608
                        " JOIN public.chat_session s ON s.id = m.session_id"  # noqa: S608
                        f" WHERE {where}{extra}"  # noqa: S608
                        " ORDER BY m.created_at DESC LIMIT :lim OFFSET :off"  # noqa: S608
                    ),
                    {**params, "lim": size, "off": (page - 1) * size},
                )
            )
            .mappings()
            .all()
        )
        return total, [dict(r) for r in rows]

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
        bool_or 覆盖多角色并集;多角色时任一 ALL 即全域可见)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT bool_or(r.data_scope = 'ALL')"  # noqa: S608
                    " FROM public.sys_role r"  # noqa: S608
                    " JOIN public.sys_user_role ur ON ur.role_id = r.id"  # noqa: S608
                    " WHERE ur.user_id = :u"  # noqa: S608
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
    """轮次范围谓词(租户收敛/时间窗);start 缺省=不限下界(补零桶不受损)。
    tenant_ids=None 不限租户(ALL 权限方无显式过滤);空列表=无可见租户(收敛
    后无绑定,产出空集而非全量)。"""
    conds = ["m.role = 'ASSISTANT'"]
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
    return " AND ".join(conds), params
