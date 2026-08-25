"""guardrail 仓储(020):sys_guardrail_audit / sys_guardrail_rule 的 SQL 落地。

审计写入=独立短事务(失败仅 error 日志不阻断,与既有审计同范式);
配置覆盖读写走请求会话。
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import get_sessionmaker, uuid7

log = logging.getLogger(__name__)

_DIGEST_MAX = 500  # content_digest 截断


class SqlGuardrailAuditWriter:
    async def record(
        self,
        *,
        trace_id: str,
        user_id: str | None,
        tenant_id: str,
        guardrail_type: str,
        rule_name: str,
        action: str,
        content_digest: str,
    ) -> None:
        try:
            async with get_sessionmaker()() as s:
                await s.execute(
                    text(
                        "INSERT INTO public.sys_guardrail_audit"
                        " (id, trace_id, user_id, tenant_id, guardrail_type,"
                        "  rule_name, action, content_digest)"
                        " VALUES (:id, :trace_id, :user_id, :tenant_id, :gt,"
                        "         :rn, :act, :cd)"
                    ),
                    {
                        "id": str(uuid7()),
                        "trace_id": trace_id[:64],
                        "user_id": user_id,
                        "tenant_id": tenant_id,
                        "gt": guardrail_type,
                        "rn": rule_name,
                        "act": action,
                        "cd": content_digest[:_DIGEST_MAX],
                    },
                )
                await s.commit()
        except Exception:
            log.error("护栏审计写入失败 trace_id=%s", trace_id, exc_info=True)


class SqlGuardrailRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def list_overrides(self) -> dict[str, dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT rule_name, enabled, action, threshold, updated_by, updated_at"
                    " FROM public.sys_guardrail_rule"
                )
            )
        ).mappings().all()
        return {str(r["rule_name"]): dict(r) for r in rows}

    async def upsert_override(
        self,
        *,
        rule_name: str,
        enabled: bool,
        action: str | None,
        threshold: int | None,
        updated_by: str,
    ) -> None:
        await self._s.execute(
            text(
                "INSERT INTO public.sys_guardrail_rule"
                " (rule_name, enabled, action, threshold, updated_by, updated_at)"
                " VALUES (:n, :e, CAST(:a AS varchar), :t, :u, now())"
                " ON CONFLICT (rule_name) DO UPDATE SET"
                " enabled = :e,"
                " action = COALESCE(CAST(:a AS varchar), public.sys_guardrail_rule.action),"
                " threshold = COALESCE(:t, public.sys_guardrail_rule.threshold),"
                " updated_by = :u, updated_at = now()"  # R1-M5:None=不触动既有覆盖
            ),
            {"n": rule_name, "e": enabled, "a": action, "t": threshold, "u": updated_by},
        )
        await self._s.commit()

    async def page_audit(
        self,
        *,
        page: int,
        size: int,
        guardrail_type: str | None = None,
        rule_name: str | None = None,
        action: str | None = None,
        user_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = ["1=1"]
        params: dict[str, Any] = {}
        if guardrail_type:
            conditions.append("guardrail_type = :gt")
            params["gt"] = guardrail_type
        if rule_name:
            conditions.append("rule_name = :rn")
            params["rn"] = rule_name
        if action:
            conditions.append("action = :act")
            params["act"] = action
        if user_id:
            conditions.append("user_id = :uid")
            params["uid"] = user_id
        if start:
            conditions.append("created_at >= :start")
            params["start"] = start
        if end:
            conditions.append("created_at < :end")
            params["end"] = end
        where = " AND ".join(conditions)
        total = (
            await self._s.execute(
                text(  # noqa: S608  # 条件片段为常量
                    f"SELECT count(*) FROM public.sys_guardrail_audit WHERE {where}"  # noqa: S608  # 条件片段为常量
                ),
                params,
            )
        ).scalar_one()
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, trace_id, user_id, tenant_id, guardrail_type,"  # noqa: S608  # 多行拼接含 f-string(条件片段常量)
                    "       rule_name, action, content_digest, created_at"
                    f" FROM public.sys_guardrail_audit WHERE {where}"
                    " ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": size, "offset": (page - 1) * size},
            )
        ).mappings().all()
        return [dict(r) for r in rows], int(total)
