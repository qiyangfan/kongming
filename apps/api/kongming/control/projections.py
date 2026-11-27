"""服务只读投影:供锦官控制台只读展示的服务域配置与运行时摘要。

字段白名单输出:凭据/token/密钥/连接串/提示词/规则正文/端点一律不返回。
不提供任何写入口,也不写库。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.control.capabilities import ADMIN_CAPABILITIES, CAPABILITIES
from kongming.control.runtime import ControlRuntime

PROJECTION_VERSION = 1


async def overview(s: AsyncSession, rt: ControlRuntime) -> dict[str, object]:
    status = await rt.status()
    counts = {
        "datasources": await _count(s, "public.sys_datasource"),
        "rag_documents": await _count(s, "rag.documents"),
        "rag_chunks": await _count(s, "rag.document_chunks"),
        "mcp_servers": await _count(s, "public.sys_mcp_server"),
        "connectors": await _count(s, "public.tool_http_connectors"),
        "workflows": await _count_distinct(s, "public.sys_workflow_def", "wf_key"),
        "triggers": await _count(s, "public.sys_trigger_definition"),
        "bots": await _count(s, "public.sys_bot_definition"),
        "roles": await _count(s, "public.sys_role"),
        "depts": await _count(s, "public.sys_dept"),
        "teams": await _count(s, "public.sys_team"),
        "users": await _count(s, "public.sys_user WHERE deleted_at IS NULL"),
    }
    return {
        "source": "kongming",
        "projection_version": PROJECTION_VERSION,
        "observed_at": _now_iso(),
        "control": {
            "mode": status.mode,
            "snapshot_version": status.snapshot_version,
            "snapshot_observed_at": (
                status.observed_at.isoformat() if status.observed_at else None
            ),
            "stale": status.stale,
            "security_ok": status.security_ok,
        },
        "capabilities": list(CAPABILITIES),
        "admin_capabilities": list(ADMIN_CAPABILITIES),
        "counts": counts,
    }


async def datasources(s: AsyncSession) -> dict[str, object]:
    rows = (
        (
            await s.execute(
                text(
                    "SELECT d.datasource_key, d.status, d.readonly, d.created_at,"
                    "       t.tenant_key"
                    " FROM public.sys_datasource d"
                    " LEFT JOIN public.sys_tenant t ON t.id = d.tenant_id"
                    " ORDER BY d.datasource_key"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {
                "datasource_key": r["datasource_key"],
                "tenant_key": r["tenant_key"],
                "status": r["status"],
                "readonly": r["readonly"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]
    }


async def rag(s: AsyncSession) -> dict[str, object]:
    rows = (
        (
            await s.execute(
                text(
                    "SELECT datasource_key, count(*) AS documents,"
                    "       coalesce(sum(chunk_count), 0) AS chunks"
                    " FROM ("
                    "   SELECT d.datasource_key, d.id,"
                    "     (SELECT count(*) FROM rag.document_chunks c WHERE c.document_id = d.id)"
                    "       AS chunk_count"
                    "   FROM rag.documents d"
                    " ) x GROUP BY datasource_key ORDER BY datasource_key"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {
                "datasource_key": r["datasource_key"],
                "documents": int(r["documents"]),
                "chunks": int(r["chunks"]),
            }
            for r in rows
        ]
    }


async def semantic(s: AsyncSession) -> dict[str, object]:
    return {
        "table_descriptions": await _count(s, "rag.table_descriptions"),
        "table_relations": await _count(s, "rag.table_relations"),
        "metric_definitions": await _count(s, "rag.metric_definitions"),
    }


async def tools() -> dict[str, object]:
    from kongming.toolcall import registry as tool_registry

    return {
        "items": [
            {
                "name": name,
                "write": spec.write,
                "require_confirm": spec.require_confirm,
                "perm_code": spec.perm_code,
            }
            for name, spec in sorted(tool_registry.effective().items())
        ]
    }


async def mcp_servers(s: AsyncSession) -> dict[str, object]:
    rows = (
        (
            await s.execute(
                text(
                    "SELECT server_key, name, enabled, last_sync_at, last_sync_error"
                    " FROM public.sys_mcp_server ORDER BY server_key"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {
                "server_key": r["server_key"],
                "name": r["name"],
                "enabled": r["enabled"],
                "last_sync_at": _iso(r["last_sync_at"]),
                "last_sync_error": (r["last_sync_error"] or "")[:200],
            }
            for r in rows
        ]
    }


async def connectors(s: AsyncSession) -> dict[str, object]:
    rows = (
        (
            await s.execute(
                text(
                    "SELECT key, name, method, enabled, requires_confirmation, timeout_ms"
                    " FROM public.tool_http_connectors ORDER BY key"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {
                "key": r["key"],
                "name": r["name"],
                "method": r["method"],
                "enabled": r["enabled"],
                "requires_confirmation": r["requires_confirmation"],
                "timeout_ms": r["timeout_ms"],
            }
            for r in rows
        ]
    }


async def workflows(s: AsyncSession) -> dict[str, object]:
    rows = (
        (
            await s.execute(
                text(
                    "SELECT DISTINCT ON (wf_key) wf_key, name, version, enabled"
                    " FROM public.sys_workflow_def ORDER BY wf_key, version DESC"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {
                "wf_key": r["wf_key"],
                "name": r["name"],
                "current_version": r["version"],
                "enabled": r["enabled"],
            }
            for r in rows
        ]
    }


async def triggers(s: AsyncSession) -> dict[str, object]:
    rows = (
        (
            await s.execute(
                text(
                    "SELECT tg_key, name, type, target_type, target_key, enabled,"
                    "       cron_expr, next_fire_at"
                    " FROM public.sys_trigger_definition ORDER BY tg_key"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {
                "tg_key": r["tg_key"],
                "name": r["name"],
                "type": r["type"],
                "target_type": r["target_type"],
                "target_key": r["target_key"],
                "enabled": r["enabled"],
                "cron_expr": r["cron_expr"],
                "next_fire_at": _iso(r["next_fire_at"]),
            }
            for r in rows
        ]
    }


async def bots(s: AsyncSession) -> dict[str, object]:
    rows = (
        (
            await s.execute(
                text(
                    "SELECT bot_key, name, enabled, is_public, datasource_key"
                    " FROM public.sys_bot_definition ORDER BY bot_key"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {
                "bot_key": r["bot_key"],
                "name": r["name"],
                "enabled": r["enabled"],
                "is_public": r["is_public"],
                "datasource_key": r["datasource_key"],
            }
            for r in rows
        ]
    }


async def guardrails(s: AsyncSession) -> dict[str, object]:
    rows = (
        (
            await s.execute(
                text(
                    "SELECT rule_name, enabled, action FROM public.sys_guardrail_rule"
                    " ORDER BY rule_name"
                )
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": [
            {"rule_name": r["rule_name"], "enabled": r["enabled"], "action": r["action"]}
            for r in rows
        ]
    }


async def identity(s: AsyncSession) -> dict[str, object]:
    roles = (
        (
            await s.execute(
                text(
                    "SELECT role_key, role_name, data_scope, sql_scope, status FROM public.sys_role"
                )
            )
        )
        .mappings()
        .all()
    )
    depts = (
        (await s.execute(text("SELECT dept_name, status FROM public.sys_dept ORDER BY dept_name")))
        .mappings()
        .all()
    )
    teams = (
        (await s.execute(text("SELECT team_key, name FROM public.sys_team ORDER BY team_key")))
        .mappings()
        .all()
    )
    return {
        "roles": [
            {
                "role_key": r["role_key"],
                "role_name": r["role_name"],
                "data_scope": r["data_scope"],
                "sql_scope": r["sql_scope"],
                "status": r["status"],
            }
            for r in roles
        ],
        "depts": [{"dept_name": r["dept_name"], "status": r["status"]} for r in depts],
        "teams": [{"team_key": r["team_key"], "name": r["name"]} for r in teams],
        "users": await _count(s, "public.sys_user WHERE deleted_at IS NULL"),
    }


PROJECTIONS = {
    "summary": None,
    "datasources": datasources,
    "rag": rag,
    "semantic": semantic,
    "mcp": mcp_servers,
    "connectors": connectors,
    "workflows": workflows,
    "triggers": triggers,
    "bots": bots,
    "guardrails": guardrails,
    "identity": identity,
}


async def project(section: str, s: AsyncSession, rt: ControlRuntime) -> dict[str, object] | None:
    if section not in PROJECTIONS:
        return None
    if section == "summary":
        return await overview(s, rt)
    if section == "tools":
        return await tools()
    fn = PROJECTIONS[section]
    if fn is None:
        return None
    return await fn(s)


async def _count(s: AsyncSession, source: str) -> int:
    """source 是本模块常量拼装(无用户输入),不接受外部字符串。"""
    # source 是本模块常量(表名 + 固定谓词),无外部输入;S608 按此豁免
    return int(
        (await s.execute(text(f"SELECT count(*) FROM {source}"))).scalar_one()  # noqa: S608
    )


async def _count_distinct(s: AsyncSession, table: str, column: str) -> int:
    return int(
        (
            await s.execute(
                text(f"SELECT count(DISTINCT {column}) FROM {table}")  # noqa: S608
            )
        ).scalar_one()
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _now_iso() -> str:
    from datetime import UTC

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
