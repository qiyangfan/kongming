"""llmusage 仓储(021):sys_llm_usage 落库与聚合查询 SQL。

无 service 层:本域零业务规则(落库+聚合直读),用例过薄不设 service
(宪法分层「组织」非每层必有;领域规则出现时再引入)。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7


class SqlLlmUsageRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def resolve_tenants(self, tenant_keys: set[str]) -> dict[str, str]:
        """tenant_key → tenant_id 批量反解(ACTIVE);未命中不进字典(调用方记 NULL)。"""
        if not tenant_keys:
            return {}
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, tenant_key FROM public.sys_tenant"
                    " WHERE tenant_key = ANY(:ks) AND status = 'ACTIVE'"
                ),
                {"ks": list(tenant_keys)},
            )
        ).all()
        return {str(r.tenant_key): str(r.id) for r in rows}

    async def insert_batch(self, records: list[dict[str, Any]]) -> int:
        for r in records:
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_llm_usage"
                    " (id, trace_id, tenant_id, model, task, prompt_tokens,"
                    "  completion_tokens, total_tokens, latency_ms, status, protocol)"
                    " VALUES (:id, :trace_id, CAST(:tenant_id AS uuid), :model, :task,"
                    "         :pt, :ct, :tt, :latency_ms, :status, :proto)"
                ),
                {
                    "id": str(uuid7()),
                    "trace_id": r.get("trace_id"),
                    "tenant_id": r.get("tenant_id"),
                    "model": r["model"],
                    "task": r["task"],
                    "pt": r.get("prompt_tokens", 0),
                    "ct": r.get("completion_tokens", 0),
                    "tt": r.get("total_tokens", 0),
                    "latency_ms": r.get("latency_ms"),
                    "status": r.get("status") or "SUCCESS",
                    "proto": r.get("protocol"),
                },
            )
        await self._s.commit()
        return len(records)

    async def insert_bot_usage_batch(
        self, records: list[dict[str, Any]], *, tenant_id: str, bot_id: str
    ) -> int:
        """035:bot 链路请求内同步落库(tenant/bot 归属=api-key 绑定值,
        不经反解);(trace_id,seq) 唯一索引兜底重试双写,冲突静默去重。
        ON CONFLICT 谓词须与 0023 部分唯一索引逐字一致(PG index inference)。
        返回真实插入行数(去重后)。自 commit(021 insert_batch 同款——
        BotService.run 调用端点无 commit 义务,用量落库须自带事务边界)。"""
        inserted = 0
        for r in records:
            res = await self._s.execute(
                text(
                    "INSERT INTO public.sys_llm_usage"
                    " (id, trace_id, tenant_id, bot_id, model, task, prompt_tokens,"
                    "  completion_tokens, total_tokens, seq, latency_ms, status, protocol)"
                    " VALUES (:id, :trace_id, CAST(:tenant_id AS uuid),"
                    "         CAST(:bot_id AS uuid), :model, :task,"
                    "         :pt, :ct, :tt, :seq, :latency_ms, :status, :proto)"
                    " ON CONFLICT (trace_id, seq)"
                    " WHERE trace_id IS NOT NULL AND bot_id IS NOT NULL"
                    " DO NOTHING"
                ),
                {
                    "id": str(uuid7()),
                    "trace_id": r.get("trace_id"),
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "model": r["model"],
                    "task": r["task"],
                    "pt": r.get("prompt_tokens", 0),
                    "ct": r.get("completion_tokens", 0),
                    "tt": r.get("total_tokens", 0),
                    "seq": int(r.get("seq", 0) or 0),
                    "latency_ms": r.get("latency_ms"),
                    "status": r.get("status") or "SUCCESS",
                    "proto": r.get("protocol"),
                },
            )
            inserted += getattr(res, "rowcount", 0) or 0
        await self._s.commit()
        return inserted

    async def summary(
        self, *, start: object = None, end: object = None, protocol: str | None = None
    ) -> list[dict[str, Any]]:
        """按租户+模型+任务聚合(时间范围可选;tenant 未反解归 unknown)。
        038:补失败次数与延迟统计——延迟仅 SUCCESS 且 latency_ms 非空参与
        (失败耗时语义归时间线,聚合指标保持成功口径干净;无样本组 avg 为
        None、max 为 None)。077:protocol 为过滤型筛选(不新增分组键)
        ——同名模型跨协议条目流量可拆分,不过滤时与现状逐字一致。"""
        conditions = ["1=1"]
        params: dict[str, object] = {}
        if start:
            conditions.append("u.created_at >= :start")
            params["start"] = start
        if end:
            conditions.append("u.created_at < :end")
            params["end"] = end
        if protocol:
            conditions.append("u.protocol = :protocol")
            params["protocol"] = protocol
        where = " AND ".join(conditions)
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT COALESCE(t.tenant_key, 'unknown') AS tenant_key,"  # noqa: S608  # 多行拼接含 f-string(条件片段常量)
                        "       u.model, u.task, count(*) AS calls,"
                        "       count(*) FILTER (WHERE u.status = 'ERROR') AS calls_error,"
                        "       sum(u.prompt_tokens) AS prompt_tokens,"
                        "       sum(u.completion_tokens) AS completion_tokens,"
                        "       sum(u.total_tokens) AS total_tokens,"
                        "       round(avg(u.latency_ms) FILTER"
                        "         (WHERE u.status = 'SUCCESS' AND u.latency_ms IS NOT NULL))"
                        "         ::integer AS latency_ms_avg,"
                        "       max(u.latency_ms) FILTER"
                        "         (WHERE u.status = 'SUCCESS' AND u.latency_ms IS NOT NULL)"
                        "         AS latency_ms_max"
                        " FROM public.sys_llm_usage u"
                        f" LEFT JOIN public.sys_tenant t ON t.id = u.tenant_id WHERE {where}"  # noqa: S608  # 条件片段为常量
                        " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def trend(self, *, days: int) -> list[dict[str, Any]]:
        """038:按天趋势(最近 days 天,含今天;Asia/Shanghai 日历日分桶
        逐天补零——月聚合时区教训先例,窗口边界与分桶必须同口径)。
        R1-B2:JOIN 附窗口下界范围谓词(created_at 可裸走时间索引);
        范围条件 MUST 在 ON 内而非 WHERE——WHERE 会把无匹配的补零桶过滤掉。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "WITH bounds AS ("
                        "  SELECT (date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')"
                        "          ::date - (:days - 1)) AS start_d,"
                        "         date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai')"
                        "           ::date AS end_d"
                        "), day_series AS ("
                        "  SELECT generate_series(b.start_d, b.end_d, '1 day')::date AS d,"
                        "         b.start_d"
                        "  FROM bounds b"
                        ")"
                        "SELECT to_char(ds.d, 'YYYY-MM-DD') AS date,"
                        "       count(u.id) AS calls,"
                        "       count(u.id) FILTER (WHERE u.status = 'ERROR') AS calls_error,"
                        "       COALESCE(sum(u.total_tokens), 0) AS total_tokens"
                        " FROM day_series ds"
                        " LEFT JOIN public.sys_llm_usage u"
                        "   ON date_trunc('day', u.created_at AT TIME ZONE 'Asia/Shanghai')"
                        "      ::date = ds.d"
                        "  AND u.created_at >= ds.start_d::timestamp"
                        "      AT TIME ZONE 'Asia/Shanghai'"
                        " GROUP BY ds.d ORDER BY ds.d"
                    ),
                    {"days": days},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def page(
        self,
        *,
        limit: int,
        offset: int,
        model: str | None = None,
        task: str | None = None,
        status: str | None = None,
        trace_id: str | None = None,
        tenant_id: str | None = None,
        protocol: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """038:明细分页(created_at 倒序;model/task/status/trace_id/tenant 筛选)。
        077:protocol 过滤 + 行含 protocol 列(null 行原样返回不炸)。
        返回 (total, rows);tenant 未反解归 unknown(展示口径);租户筛选经
        u.tenant_id 直滤——该租户全部行均返回,含 tenant_key 反解失败展示为
        unknown 的行(展示 unknown ≠ 无 tenant_id,038③ 清偿口径)。"""
        conditions = ["1=1"]
        params: dict[str, object] = {"limit": limit, "offset": offset}
        if model:
            conditions.append("u.model = :model")
            params["model"] = model
        if task:
            conditions.append("u.task = :task")
            params["task"] = task
        if status:
            conditions.append("u.status = :status")
            params["status"] = status
        if trace_id:
            conditions.append("u.trace_id = :trace_id")
            params["trace_id"] = trace_id
        if tenant_id:
            conditions.append("u.tenant_id = CAST(:tenant_id AS uuid)")
            params["tenant_id"] = tenant_id
        if protocol:
            conditions.append("u.protocol = :protocol")
            params["protocol"] = protocol
        where = " AND ".join(conditions)
        total = int(
            (
                await self._s.execute(
                    text(
                        "SELECT count(*) FROM public.sys_llm_usage u"  # noqa: S608  # 条件片段为常量
                        f" WHERE {where}"
                    ),
                    params,
                )
            ).scalar_one()
        )
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT u.id, u.trace_id,"  # noqa: S608  # 多行拼接含 f-string(条件片段常量)
                        " COALESCE(t.tenant_key, 'unknown') AS tenant_key,"
                        " u.model, u.task, u.status, u.bot_id,"
                        " u.prompt_tokens, u.completion_tokens, u.total_tokens,"
                        " u.latency_ms, u.created_at, u.protocol"
                        " FROM public.sys_llm_usage u"
                        f" LEFT JOIN public.sys_tenant t ON t.id = u.tenant_id WHERE {where}"
                        " ORDER BY u.created_at DESC, u.id DESC"
                        " LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return total, [dict(r) for r in rows]

    async def list_by_trace(self, *, trace_id: str) -> list[dict[str, Any]]:
        """038:trace 时间线 LLM 事件(时间升序;稳定序 tiebreak=id)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT model, task, status, prompt_tokens, completion_tokens,"
                        " total_tokens, latency_ms, created_at"
                        " FROM public.sys_llm_usage WHERE trace_id = :tid"
                        " ORDER BY created_at ASC, id ASC"
                    ),
                    {"tid": trace_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]
