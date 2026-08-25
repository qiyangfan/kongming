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
                    "  completion_tokens, total_tokens)"
                    " VALUES (:id, :trace_id, CAST(:tenant_id AS uuid), :model, :task,"
                    "         :pt, :ct, :tt)"
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
                    "  completion_tokens, total_tokens, seq)"
                    " VALUES (:id, :trace_id, CAST(:tenant_id AS uuid),"
                    "         CAST(:bot_id AS uuid), :model, :task,"
                    "         :pt, :ct, :tt, :seq)"
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
                },
            )
            inserted += getattr(res, "rowcount", 0) or 0
        await self._s.commit()
        return inserted

    async def summary(
        self, *, start: object = None, end: object = None
    ) -> list[dict[str, Any]]:
        """按租户+模型+任务聚合(时间范围可选;tenant 未反解归 unknown)。"""
        conditions = ["1=1"]
        params: dict[str, object] = {}
        if start:
            conditions.append("u.created_at >= :start")
            params["start"] = start
        if end:
            conditions.append("u.created_at < :end")
            params["end"] = end
        where = " AND ".join(conditions)
        rows = (
            await self._s.execute(
                text(
                    "SELECT COALESCE(t.tenant_key, 'unknown') AS tenant_key,"  # noqa: S608  # 多行拼接含 f-string(条件片段常量)
                    "       u.model, u.task, count(*) AS calls,"
                    "       sum(u.prompt_tokens) AS prompt_tokens,"
                    "       sum(u.completion_tokens) AS completion_tokens,"
                    "       sum(u.total_tokens) AS total_tokens"
                    " FROM public.sys_llm_usage u"
                    f" LEFT JOIN public.sys_tenant t ON t.id = u.tenant_id WHERE {where}"  # noqa: S608  # 条件片段为常量
                    " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
                ),
                params,
            )
        ).mappings().all()
        return [dict(r) for r in rows]
