"""内部执行编排:租户解析 → 绑定复校 → 防火墙 → data_scope → 复检 → 只读执行 → 审计。

sql-execution spec 落地:
- fail-closed 三面:未知/禁用租户、用户-租户未绑定、非 default 数据源(动态池未落地);
- 系统调用(user_id 缺省)豁免绑定复校与 data_scope,其余防御层不变;
- 改写产物必过防火墙复检 + schema 边界校验(防御性,防改写引入非法语句/跨租户引用);
- PASS/BLOCKED/ERROR 三态全落审计,trace_id 原样贯穿,sql_text 记实际执行形态;
- 编排段基础设施异常(连接池超时等)同样收敛为 ERROR 落审计,不裸 500;
- ERROR 的 reason 取驱动层整洁摘要(剥 SQLAlchemy asyncpg 包装类前缀),供 selfheal 喂回。
"""

import logging
import re
import time
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.audit.models import SqlAuditEntry
from kongming.audit.writer import SqlAuditWriter
from kongming.datascope import applier
from kongming.datascope.models import AppliedSQL, DataScopeUnsupported
from kongming.internal.executor import get_read_engine
from kongming.sqlfw.firewall import check, check_schema_scope

log = logging.getLogger(__name__)


class TenantNotFound(Exception):
    """tenant_key 不存在或已禁用(对外同形,不暴露是哪种)。"""


class TenantBindingForbidden(Exception):
    """用户未绑定目标租户(防 agent 侧越权代理)。"""


class UnsupportedDatasource(Exception):
    """非 default 数据源:动态池未落地,fail-closed 不静默回退。"""


# SQLAlchemy asyncpg 方言把驱动错误包成 DBAPIError,args[0] 形如
# "<class 'asyncpg.exceptions.X'>: 真实消息"——剥掉类路径前缀喂 selfheal
_DRIVER_PREFIX = re.compile(r"^<class '[^']+'>: ")


def _error_summary(e: Exception) -> str:
    orig = getattr(e, "orig", None)  # 方言包装层;取不到就用外层
    raw = str(orig.args[0] if orig is not None and orig.args else e)
    return _DRIVER_PREFIX.sub("", raw)


@dataclass(frozen=True)
class ExecResult:
    verdict: str  # PASS / BLOCKED / ERROR
    reason: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    row_count: int = 0
    duration_ms: int = 0


async def execute(  # noqa: PLR0913 —— 请求字段平铺,编排入口
    session: AsyncSession,
    *,
    trace_id: str,
    tenant_key: str,
    datasource_key: str,
    sql_text: str,
    user_id: str | None,
    question: str | None,
    limit: int,
) -> ExecResult:
    started = time.monotonic()
    writer = SqlAuditWriter()

    # 1. 租户解析(fail-closed:不存在/禁用同形拒绝;此时无租户主体,不落审计)
    tenant = (
        await session.execute(
            text("SELECT id, status FROM public.sys_tenant WHERE tenant_key = :k"),
            {"k": tenant_key},
        )
    ).mappings().one_or_none()
    if tenant is None or tenant["status"] != "ACTIVE":
        raise TenantNotFound(tenant_key)
    tenant_id = str(tenant["id"])

    # 2. 数据源:仅 default(动态数据源池后续变更插槽)
    if datasource_key != "default":
        raise UnsupportedDatasource(datasource_key)

    def _elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    async def _finalize(verdict: str, audited_sql: str, reason: str) -> ExecResult:
        """BLOCKED/ERROR 统一收尾:落审计 + 组装响应(先写审计后返回)。"""
        await writer.record(
            SqlAuditEntry(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=question,
                sql_text=audited_sql,
                verdict=verdict,  # type: ignore[arg-type]  # 调用方仅传 BLOCKED/ERROR
                block_reason=reason,
                duration_ms=_elapsed(),
            )
        )
        return ExecResult(verdict=verdict, reason=reason, duration_ms=_elapsed())

    # 3. 用户-租户绑定复校(系统调用豁免);基础设施异常收敛 ERROR 不裸 500
    if user_id is not None:
        try:
            bound = (
                await session.execute(
                    text(
                        "SELECT 1 FROM public.sys_user_tenant"
                        " WHERE user_id = :u AND tenant_id = :t"
                    ),
                    {"u": user_id, "t": tenant_id},
                )
            ).scalar_one_or_none()
        except Exception as e:
            return await _finalize("ERROR", sql_text, _error_summary(e))
        if bound is None:
            raise TenantBindingForbidden(f"{user_id} 未绑定租户 {tenant_key}")

    # 4. 防火墙初检(原 SQL)
    first = check(sql_text)
    if not first.passed:
        return await _finalize("BLOCKED", sql_text, first.reason or "BLOCKED")

    # 5. data_scope 改写(系统调用豁免;CUSTOM/未知值 fail-closed)
    schema_name = f"tenant_{tenant_key}"
    applied = AppliedSQL(sql=sql_text)
    if user_id is not None:
        try:
            async with get_read_engine().connect() as probe_conn:
                applied = await applier.apply(
                    session,
                    probe_conn,
                    user_id=user_id,
                    tenant_schemas=[schema_name, "public"],
                    sql=sql_text,
                )
        except DataScopeUnsupported as e:
            return await _finalize("BLOCKED", sql_text, f"DATA_SCOPE_UNSUPPORTED: {e.reason}")
        except Exception as e:
            # 编排段基础设施异常(池超时/控制库抖动):ERROR 落审计,不裸 500
            return await _finalize("ERROR", sql_text, _error_summary(e))
        if applied.warning:
            log.warning("data_scope 降级: %s (trace_id=%s)", applied.warning, trace_id)

    # 6. 改写产物复检:防火墙 + schema 边界(纵深防御,datascope spec 清单 10)
    second = check(applied.sql)
    if not second.passed:
        log.error("改写产物未过防火墙复检 trace_id=%s reason=%s", trace_id, second.reason)
        return await _finalize("BLOCKED", applied.sql, f"POST_REWRITE: {second.reason}")
    scope_verdict = check_schema_scope(applied.sql, {schema_name, "public"})
    if not scope_verdict.passed:
        return await _finalize("BLOCKED", applied.sql, scope_verdict.reason or "CROSS_SCHEMA")

    # 7. 只读池执行:事务内 SET LOCAL search_path + 行数截断
    quoted_schema = schema_name.replace('"', '""')
    try:
        async with get_read_engine().connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text(f'SET LOCAL search_path TO "{quoted_schema}", public')  # noqa: S608  # 标识符双引号转义,非值拼接
                )
                result = await conn.execute(text(applied.sql), applied.params)
                columns = [str(c) for c in result.keys()]
                rows = [list(r) for r in result.fetchmany(limit)]
    except Exception as e:
        return await _finalize("ERROR", applied.sql, _error_summary(e))

    await writer.record(
        SqlAuditEntry(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            sql_text=applied.sql,
            verdict="PASS",
            row_count=len(rows),
            duration_ms=_elapsed(),
        )
    )
    return ExecResult(
        verdict="PASS",
        columns=columns,
        rows=rows,
        row_count=len(rows),
        duration_ms=_elapsed(),
    )
