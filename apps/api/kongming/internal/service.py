"""Agent 内部 SQL 执行入口。

先复核租户、数据源和绑定关系，再依次执行 SQL 防火墙、数据范围改写与复检，
最后进入只读连接池并记录审计。未知租户、未绑定用户或无效数据源一律拒绝；
系统调用只跳过用户绑定和数据范围检查；改写后的 SQL 仍需通过防火墙与
schema 检查。结果均记录为 PASS、BLOCKED 或 ERROR；执行与 EXPLAIN 共用
相同的前置检查。"""

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from kongming.audit.models import SqlAuditEntry
from kongming.audit.writer import SqlAuditWriter
from kongming.config import get_settings
from kongming.datascope import applier
from kongming.datascope.models import AppliedSQL, DataScopeUnsupported
from kongming.datasource.cipher import get_cipher
from kongming.datasource.models import CredentialUndecryptable
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.internal.executor import get_or_create_engine
from kongming.sqlfw import guard

log = logging.getLogger(__name__)


class TenantNotFound(Exception):
    """tenant_key 不存在或已禁用(对外同形,不暴露是哪种)。"""


class TenantBindingForbidden(Exception):
    """用户未绑定目标租户(防 agent 侧越权代理)。"""


class InvalidDatasource(Exception):
    """数据源不存在/禁用/越属:同形 400 DATASOURCE_INVALID(防 key 探测)。"""


async def resolve_tenant(session: AsyncSession, tenant_key: str) -> str:
    """租户解析(fail-closed:不存在/禁用同形拒绝;此时无租户主体,不落审计)。

    internal 执行前段与 toolcall 六层防御 L1 共用本实现,保证防线口径单一。
    """
    tenant = (
        (
            await session.execute(
                text("SELECT id, status FROM public.sys_tenant WHERE tenant_key = :k"),
                {"k": tenant_key},
            )
        )
        .mappings()
        .one_or_none()
    )
    if tenant is None or tenant["status"] != "ACTIVE":
        raise TenantNotFound(tenant_key)
    return str(tenant["id"])


async def assert_user_bound(
    session: AsyncSession, *, user_id: str, tenant_id: str, tenant_key: str
) -> None:
    """用户-租户绑定复校;系统调用豁免语义由调用方决定,本函数恒校验。"""
    bound = (
        await session.execute(
            text("SELECT 1 FROM public.sys_user_tenant WHERE user_id = :u AND tenant_id = :t"),
            {"u": user_id, "t": tenant_id},
        )
    ).scalar_one_or_none()
    if bound is None:
        raise TenantBindingForbidden(f"{user_id} 未绑定租户 {tenant_key}")


# SQLAlchemy asyncpg 方言把驱动错误包成 DBAPIError,args[0] 形如
# "<class 'asyncpg.exceptions.X'>: 真实消息"——剥掉类路径前缀喂 selfheal
_DRIVER_PREFIX = re.compile(r"^<class '[^']+'>: ")


def _error_summary(e: Exception) -> str:
    orig = getattr(e, "orig", None)  # 方言包装层;取不到就用外层
    raw = str(orig.args[0] if orig is not None and orig.args else e)
    return _DRIVER_PREFIX.sub("", raw)


def _json_cell(v: object) -> object:
    """单元格 JSON 友好化:asyncpg 的 numeric→Decimal 在 JSON 里是字符串,
    下游图表推断/stats 会判成非数值;转 float 保数值语义。

    取舍:>2⁵³ 的 numeric 转 float 有末位精度损失(展示路径可接受,审计不存
    行数据);NaN/±Infinity 非有限值 JSON 无法表达(裸 NaN 字面量会让前端
    JSON.parse 抛错),映射为 None。
    """
    if isinstance(v, Decimal):
        f = float(v)
        return f if math.isfinite(f) else None
    return v


@dataclass(frozen=True)
class ExecResult:
    verdict: str  # PASS / BLOCKED / ERROR
    reason: str | None = None
    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
    row_count: int = 0
    duration_ms: int = 0


@dataclass(frozen=True)
class ExplainResult:
    """执行计划结果:PASS 时 plan=EXPLAIN (FORMAT JSON) 解析产物(顶层数组)。"""

    verdict: str  # PASS / BLOCKED / ERROR
    reason: str | None = None
    plan: object | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class _Prepared:
    """执行前段产物(execute/explain 共享):全部防线通过后的执行上下文。"""

    tenant_id: str
    engine: AsyncEngine
    route_schema: str
    applied: AppliedSQL


async def _finalize(ctx: "_ExecContext", verdict: str, sql_text: str, reason: str) -> ExecResult:
    """BLOCKED/ERROR 统一收尾:落审计 + 组装响应(先写审计后返回)。"""
    elapsed = int((time.monotonic() - ctx.started) * 1000)
    await ctx.writer.record(
        SqlAuditEntry(
            trace_id=ctx.trace_id,
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            datasource_key=ctx.datasource_key,
            question=ctx.question,
            sql_text=sql_text,
            verdict=verdict,  # type: ignore[arg-type]  # 调用方仅传 BLOCKED/ERROR
            block_reason=reason,
            duration_ms=elapsed,
        )
    )
    return ExecResult(verdict=verdict, reason=reason, duration_ms=elapsed)


async def _prepare(
    session: AsyncSession,
    *,
    trace_id: str,
    tenant_key: str,
    datasource_key: str,
    sql_text: str,
    user_id: str | None,
    question: str | None,
    started: float,
) -> _Prepared | ExecResult:
    """执行前段(execute/explain 共享,防线顺序即 spec 立约):
    租户解析 → 数据源解析 → 绑定复校 → 防火墙初检 → data_scope 改写 → 复检三件套。
    任一不过:返回已落审计的 ExecResult(BLOCKED/ERROR);全过返回 _Prepared。
    """
    ctx = _ExecContext(
        writer=SqlAuditWriter(),
        started=started,
        trace_id=trace_id,
        user_id=user_id,
        datasource_key=datasource_key,
        sql_text=sql_text,
        question=question,
    )
    tenant_id = await resolve_tenant(session, tenant_key)
    ctx.tenant_id = tenant_id  # 后续数据源归属校验与审计均按此租户
    outcome = await _apply_defenses(ctx, session, tenant_key=tenant_key)
    if isinstance(outcome, ExecResult):
        return outcome
    engine, route_schema, applied = outcome
    return _Prepared(tenant_id=tenant_id, engine=engine, route_schema=route_schema, applied=applied)


async def _apply_defenses(
    ctx: "_ExecContext", session: AsyncSession, *, tenant_key: str
) -> tuple[AsyncEngine, str, AppliedSQL] | ExecResult:
    """按 spec 顺序过数据源/防火墙/改写/复检四道防线;被拦返回已落审计的 ExecResult。"""
    # 2-3. 数据源解析与绑定复校(失败即返回已落审计的 ExecResult)
    target = await _resolve_target(
        ctx, session, datasource_key=ctx.datasource_key, tenant_key=tenant_key
    )
    if isinstance(target, ExecResult):
        return target
    engine, route_schema = target

    # 4. 防火墙初检(原 SQL;单遍解析:初检/改写结构共享同一 Guarded)
    src = guard.parse_guarded(ctx.sql_text)
    first = src.check()
    if not first.passed:
        return await ctx.fin("BLOCKED", ctx.sql_text, first.reason or "BLOCKED")

    # 5. data_scope 改写(系统调用豁免;CUSTOM/未知值 fail-closed)
    applied = await _apply_data_scope(
        ctx,
        session,
        engine,
        src,
        user_id=ctx.user_id,
        datasource_key=ctx.datasource_key,
        sql_text=ctx.sql_text,
        route_schema=route_schema,
    )
    if isinstance(applied, ExecResult):
        return applied

    # 6. 改写产物复检:防火墙 + 保留表拒读 + schema 边界(纵深防御,datascope spec 清单 10)
    blocked = await _recheck_rewrite(
        ctx, applied, datasource_key=ctx.datasource_key, route_schema=route_schema
    )
    if blocked is not None:
        return blocked
    return engine, route_schema, applied


@dataclass
class _ExecContext:
    """前段收尾上下文:审计写入器与请求字段(失败路径统一经 fin 落审计)。"""

    writer: SqlAuditWriter
    started: float
    trace_id: str
    user_id: str | None
    datasource_key: str
    sql_text: str
    question: str | None
    tenant_id: str = ""

    async def fin(self, verdict: str, sql: str, reason: str) -> ExecResult:
        return await _finalize(self, verdict, sql, reason)


async def _resolve_target(
    ctx: _ExecContext, session: AsyncSession, *, datasource_key: str, tenant_key: str
) -> tuple[AsyncEngine, str] | ExecResult:
    """数据源解析 + 绑定复校,返回 (执行引擎, 路由 schema) 或已落审计的失败结果。

    default 走平台配置连接串;非 default 校验存在+启用+归属当前租户——任一不满足:
    审计 BLOCKED(分类内部可溯)+ 同形 InvalidDatasource(对外不区分原因,防 key
    探测),不静默回退默认路径。"""
    engine_dsn: URL | None = None
    route_schema = f"tenant_{tenant_key}"
    if datasource_key != "default":
        row = await SqlDatasourceRepo(session).find_for_execute(datasource_key)
        category: str | None = None
        if row is None:
            category = "NOT_FOUND"
        elif row.status != "ACTIVE":
            category = "DISABLED"
        elif row.tenant_id != ctx.tenant_id:
            category = "CROSS_TENANT"
        if category is not None or row is None:
            await ctx.fin("BLOCKED", ctx.sql_text, f"DATASOURCE_INVALID: {category}")
            raise InvalidDatasource(datasource_key)
        try:
            plain = get_cipher(get_settings().datasource_encrypt_key).decrypt(row.password_enc)
        except CredentialUndecryptable:
            # 密钥轮换/密文损坏:凭证不可用归 ERROR(不暴露密文与密钥细节)
            return await ctx.fin("ERROR", ctx.sql_text, "DATASOURCE_CREDENTIAL_UNAVAILABLE")
        engine_dsn = URL.create(
            "postgresql+asyncpg",
            username=row.username,
            password=plain,  # 仅存内存局部变量;URL.create 负责转义
            host=row.host,
            port=row.port,
            database=row.database,
        )
        route_schema = row.default_schema
    engine = await get_or_create_engine(datasource_key, engine_dsn)

    # 复核用户与租户绑定；系统调用免检，校验异常统一记为 ERROR。
    if ctx.user_id is not None:
        try:
            await assert_user_bound(
                session, user_id=ctx.user_id, tenant_id=ctx.tenant_id, tenant_key=tenant_key
            )
        except TenantBindingForbidden:
            raise
        except Exception as e:  # noqa: BLE001
            return await ctx.fin("ERROR", ctx.sql_text, _error_summary(e))
    return engine, route_schema


async def _apply_data_scope(
    ctx: _ExecContext,
    session: AsyncSession,
    engine: AsyncEngine,
    src: guard.Guarded,
    *,
    user_id: str | None,
    datasource_key: str,
    sql_text: str,
    route_schema: str,
) -> AppliedSQL | ExecResult:
    """data_scope 改写;系统调用豁免,失败返回已落审计的 BLOCKED/ERROR。

    列探测走执行目标库连接(探测集与 scope 白名单同源:default 路径仅租户
    schema——平台库 public 是控制面表,租户 SQL 不得触;动态源业务库
    业务表落 public,保留 public 与 search_path 对齐)。"""
    applied = AppliedSQL(sql=sql_text)
    allowed_schemas = {route_schema} if datasource_key == "default" else {route_schema, "public"}
    if user_id is None:
        return applied
    try:
        async with engine.connect() as probe_conn:
            applied = await applier.apply(
                session,
                probe_conn,
                user_id=user_id,
                tenant_schemas=sorted(allowed_schemas),
                sql=sql_text,
                guarded=src,
            )
    except DataScopeUnsupported as e:
        return await ctx.fin("BLOCKED", sql_text, f"DATA_SCOPE_UNSUPPORTED: {e.reason}")
    except Exception as e:  # noqa: BLE001
        # 连接池超时或控制库异常均写入审计并返回 ERROR。
        return await ctx.fin("ERROR", sql_text, _error_summary(e))
    if applied.warning:
        log.warning("data_scope 降级: %s (trace_id=%s)", applied.warning, ctx.trace_id)
    return applied


async def _recheck_rewrite(
    ctx: _ExecContext,
    applied: AppliedSQL,
    *,
    datasource_key: str,
    route_schema: str,
) -> ExecResult | None:
    """改写产物三件套复检(防火墙 → 保留表 → schema 边界);不过返回已落审计的 BLOCKED。"""
    allowed_schemas = {route_schema} if datasource_key == "default" else {route_schema, "public"}
    out = guard.parse_guarded(applied.sql)
    second = out.check()
    if not second.passed:
        log.error("改写产物未过防火墙复检 trace_id=%s reason=%s", ctx.trace_id, second.reason)
        return await ctx.fin("BLOCKED", applied.sql, f"POST_REWRITE: {second.reason}")
    reserved = out.check_reserved_tables()
    if not reserved.passed:
        return await ctx.fin("BLOCKED", applied.sql, reserved.reason or "RESERVED_TABLE")
    scope_verdict = out.check_schema_scope(allowed_schemas)
    if not scope_verdict.passed:
        return await ctx.fin("BLOCKED", applied.sql, scope_verdict.reason or "CROSS_SCHEMA")
    return None


def _request_ctx(
    *,
    started: float,
    trace_id: str,
    user_id: str | None,
    datasource_key: str,
    sql_text: str,
    question: str | None,
    prepared: _Prepared,
) -> "_ExecContext":
    """按已备好的执行上下文组收尾上下文(租户取前段解析结果),供落审计与失败返回复用。"""
    return _ExecContext(
        writer=SqlAuditWriter(),
        started=started,
        trace_id=trace_id,
        user_id=user_id,
        datasource_key=datasource_key,
        sql_text=sql_text,
        question=question,
        tenant_id=prepared.tenant_id,
    )


async def _record_pass(ctx: "_ExecContext", sql: str, row_count: int, elapsed: int) -> None:
    """记一条 PASS 审计(sql 为实际执行文本)。"""
    await ctx.writer.record(
        SqlAuditEntry(
            trace_id=ctx.trace_id,
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            datasource_key=ctx.datasource_key,
            question=ctx.question,
            sql_text=sql,
            verdict="PASS",
            row_count=row_count,
            duration_ms=elapsed,
        )
    )


async def execute(
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
    prepared = await _prepare(
        session,
        trace_id=trace_id,
        tenant_key=tenant_key,
        datasource_key=datasource_key,
        sql_text=sql_text,
        user_id=user_id,
        question=question,
        started=started,
    )
    if isinstance(prepared, ExecResult):
        return prepared  # 前段已拦(BLOCKED/ERROR 已落审计)

    applied = prepared.applied
    # 7. 只读池执行:事务内 SET LOCAL search_path + 行数截断
    try:
        columns, rows = await _fetch_capped(prepared, applied, limit)
    except Exception as e:  # noqa: BLE001
        return await _request_ctx(
            started=started,
            trace_id=trace_id,
            user_id=user_id,
            datasource_key=datasource_key,
            sql_text=sql_text,
            question=question,
            prepared=prepared,
        ).fin("ERROR", applied.sql, _error_summary(e))

    elapsed = int((time.monotonic() - started) * 1000)
    await _record_pass(
        _request_ctx(
            started=started,
            trace_id=trace_id,
            user_id=user_id,
            datasource_key=datasource_key,
            sql_text=sql_text,
            question=question,
            prepared=prepared,
        ),
        applied.sql,
        len(rows),
        elapsed,
    )
    return ExecResult(
        verdict="PASS",
        columns=columns,
        rows=rows,
        row_count=len(rows),
        duration_ms=elapsed,
    )


async def _fetch_capped(
    prepared: _Prepared, applied: AppliedSQL, limit: int
) -> tuple[list[str], list[list[object]]]:
    """只读池执行改写后的 SQL 并截断到 limit 行,返回 (列名, 行值)。"""
    quoted_schema = prepared.route_schema.replace('"', '""')
    async with prepared.engine.connect() as conn, conn.begin():
        await conn.execute(
            text(f'SET LOCAL search_path TO "{quoted_schema}", public')  # 标识符双引号转义,非值拼接
        )
        result = await conn.execute(text(applied.sql), applied.params)
        # SQLAlchemy Result 不是 dict:显式取 keys()(SIM118 对 .keys() 迭代误报)
        columns = [str(c) for c in list(result.keys())]
        rows = [[_json_cell(v) for v in r] for r in result.fetchmany(limit)]
    return columns, rows


async def explain(
    session: AsyncSession,
    *,
    trace_id: str,
    tenant_key: str,
    datasource_key: str,
    sql_text: str,
    user_id: str | None,
    question: str | None,
) -> ExplainResult:
    """执行计划(chat spec「EXPLAIN 执行计划」):防线与 execute 全同(_prepare
    共享),复检通过后由服务端拼 `EXPLAIN (FORMAT JSON)` 前缀——EXPLAIN 文本永不进
    防火墙 check(无新攻击面),永不生成 ANALYZE(不真执行目标 SQL)。
    """
    started = time.monotonic()
    prepared = await _prepare(
        session,
        trace_id=trace_id,
        tenant_key=tenant_key,
        datasource_key=datasource_key,
        sql_text=sql_text,
        user_id=user_id,
        question=question,
        started=started,
    )
    if isinstance(prepared, ExecResult):
        return ExplainResult(
            verdict=prepared.verdict, reason=prepared.reason, duration_ms=prepared.duration_ms
        )

    ctx = _request_ctx(
        started=started,
        trace_id=trace_id,
        user_id=user_id,
        datasource_key=datasource_key,
        sql_text=sql_text,
        question=question,
        prepared=prepared,
    )
    explain_sql = f"EXPLAIN (FORMAT JSON) {prepared.applied.sql}"
    try:
        plan = _parse_plan(await _explain_raw(prepared))
    except Exception as e:  # noqa: BLE001
        finalized = await ctx.fin("ERROR", explain_sql, _error_summary(e))
        return _explain_error(finalized)

    elapsed = int((time.monotonic() - started) * 1000)
    # EXPLAIN 形态落审计(可回放,与 PASS 口径一致)
    await _record_pass(ctx, explain_sql, 0, elapsed)
    return ExplainResult(verdict="PASS", plan=plan, duration_ms=elapsed)


def _explain_error(finalized: ExecResult) -> ExplainResult:
    """执行失败时把审计收尾结果转成 ExplainResult(仅保留结论、原因与耗时)。"""
    return ExplainResult(
        verdict="ERROR", reason=finalized.reason, duration_ms=finalized.duration_ms
    )


async def _explain_raw(prepared: _Prepared) -> object:
    """在只读池执行 EXPLAIN (FORMAT JSON),返回单行单列结果原值。"""
    applied = prepared.applied
    quoted_schema = prepared.route_schema.replace('"', '""')
    explain_sql = f"EXPLAIN (FORMAT JSON) {applied.sql}"
    async with prepared.engine.connect() as conn, conn.begin():
        await conn.execute(
            text(f'SET LOCAL search_path TO "{quoted_schema}", public')  # 标识符双引号转义,非值拼接
        )
        result = await conn.execute(text(explain_sql), applied.params)
        return result.scalar_one()


def _parse_plan(raw: object) -> object:
    """EXPLAIN (FORMAT JSON) 返回单行单列 json;asyncpg 对 json 列给 str,解析失败降级 raw。"""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
    return raw
