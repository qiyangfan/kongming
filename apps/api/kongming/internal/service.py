"""内部执行编排:租户解析 → 数据源解析 → 绑定复校 → 防火墙 → data_scope → 复检 → 只读执行 → 审计。

sql-execution spec 落地:
- fail-closed 三面:未知/禁用租户、用户-租户未绑定、数据源非法(008 动态化:
  非 default 校验存在+启用+归属当前租户,同形 400 DATASOURCE_INVALID 防 key 探测,
  审计落 BLOCKED+分类内部可溯;不静默回退默认路径);
- 系统调用(user_id 缺省)豁免绑定复校与 data_scope,其余防御层不变;
- 改写产物必过防火墙复检 + schema 边界校验(防御性,防改写引入非法语句/跨租户引用);
- PASS/BLOCKED/ERROR 三态全落审计,trace_id 原样贯穿,sql_text 记实际执行形态;
- 编排段基础设施异常(连接池超时等)同样收敛为 ERROR 落审计,不裸 500;
- ERROR 的 reason 取驱动层整洁摘要(剥 SQLAlchemy asyncpg 包装类前缀),供 selfheal 喂回。

010 注记:前段(1-6)提取为 _prepare,execute/explain 两入口共享——防线单一事实源,
EXPLAIN 通道不复制安全逻辑(design D2)。
"""

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
from kongming.sqlfw.firewall import check, check_reserved_tables, check_schema_scope

log = logging.getLogger(__name__)


class TenantNotFound(Exception):
    """tenant_key 不存在或已禁用(对外同形,不暴露是哪种)。"""


class TenantBindingForbidden(Exception):
    """用户未绑定目标租户(防 agent 侧越权代理)。"""


class InvalidDatasource(Exception):
    """数据源不存在/禁用/越属:同形 400 DATASOURCE_INVALID(防 key 探测)。"""


async def resolve_tenant(session: AsyncSession, tenant_key: str) -> str:
    """租户解析(fail-closed:不存在/禁用同形拒绝;此时无租户主体,不落审计)。

    019 抽共用:internal 执行前段与 toolcall 六层防御 L1 同调(防线单一事实源)。
    """
    tenant = (
        await session.execute(
            text("SELECT id, status FROM public.sys_tenant WHERE tenant_key = :k"),
            {"k": tenant_key},
        )
    ).mappings().one_or_none()
    if tenant is None or tenant["status"] != "ACTIVE":
        raise TenantNotFound(tenant_key)
    return str(tenant["id"])


async def assert_user_bound(
    session: AsyncSession, *, user_id: str, tenant_id: str, tenant_key: str
) -> None:
    """用户-租户绑定复校(019 抽共用;系统调用豁免语义由调用方决定,本函数恒校验)。"""
    bound = (
        await session.execute(
            text(
                "SELECT 1 FROM public.sys_user_tenant"
                " WHERE user_id = :u AND tenant_id = :t"
            ),
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
    下游图表推断/stats 会判成非数值(005 冒烟实证);转 float 保数值语义。

    取舍(钉住,R2 评审):>2⁵³ 的 numeric 转 float 有末位精度损失(展示路径
    可接受,审计不存行数据);NaN/±Infinity 非有限值 JSON 无法表达(裸 NaN
    字面量会让前端 JSON.parse 抛错),映射为 None。
    """
    if isinstance(v, Decimal):
        f = float(v)
        return f if math.isfinite(f) else None
    return v


@dataclass(frozen=True)
class ExecResult:
    verdict: str  # PASS / BLOCKED / ERROR
    reason: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[object]] = field(default_factory=list)
    row_count: int = 0
    duration_ms: int = 0


@dataclass(frozen=True)
class ExplainResult:
    """执行计划结果(010):PASS 时 plan=EXPLAIN (FORMAT JSON) 解析产物(顶层数组)。"""

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


async def _finalize(  # noqa: PLR0913 —— 审计上下文字段平铺
    writer: SqlAuditWriter,
    started: float,
    *,
    trace_id: str,
    user_id: str | None,
    tenant_id: str,
    datasource_key: str,
    question: str | None,
    verdict: str,
    sql_text: str,
    reason: str,
) -> ExecResult:
    """BLOCKED/ERROR 统一收尾:落审计 + 组装响应(先写审计后返回)。"""
    elapsed = int((time.monotonic() - started) * 1000)
    await writer.record(
        SqlAuditEntry(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            sql_text=sql_text,
            verdict=verdict,  # type: ignore[arg-type]  # 调用方仅传 BLOCKED/ERROR
            block_reason=reason,
            duration_ms=elapsed,
        )
    )
    return ExecResult(verdict=verdict, reason=reason, duration_ms=elapsed)


async def _prepare(  # noqa: PLR0913 —— 请求字段平铺,编排前段
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
    writer = SqlAuditWriter()

    # 1. 租户解析(公共函数,019 抽共用;fail-closed 不落审计)
    tenant_id = await resolve_tenant(session, tenant_key)

    async def _fin(verdict: str, sql: str, reason: str) -> ExecResult:
        return await _finalize(
            writer,
            started,
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            verdict=verdict,
            sql_text=sql,
            reason=reason,
        )

    # 2. 数据源解析(008 动态化):default 走平台配置连接串;非 default 校验
    #    存在+启用+归属当前租户——任一不满足:审计 BLOCKED(分类内部可溯)+
    #    同形 InvalidDatasource(对外不区分原因,防 key 探测);不静默回退默认路径
    engine_dsn: URL | None = None
    route_schema = f"tenant_{tenant_key}"
    if datasource_key != "default":
        row = await SqlDatasourceRepo(session).find_for_execute(datasource_key)
        category: str | None = None
        if row is None:
            category = "NOT_FOUND"
        elif row.status != "ACTIVE":
            category = "DISABLED"
        elif row.tenant_id != tenant_id:
            category = "CROSS_TENANT"
        if category is not None or row is None:
            await _fin("BLOCKED", sql_text, f"DATASOURCE_INVALID: {category}")
            raise InvalidDatasource(datasource_key)
        try:
            plain = get_cipher(get_settings().datasource_encrypt_key).decrypt(row.password_enc)
        except CredentialUndecryptable:
            # 密钥轮换/密文损坏:凭证不可用归 ERROR(不暴露密文与密钥细节)
            return await _fin("ERROR", sql_text, "DATASOURCE_CREDENTIAL_UNAVAILABLE")
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

    # 3. 用户-租户绑定复校(系统调用豁免;公共函数);基础设施异常收敛 ERROR 不裸 500
    if user_id is not None:
        try:
            await assert_user_bound(
                session, user_id=user_id, tenant_id=tenant_id, tenant_key=tenant_key
            )
        except TenantBindingForbidden:
            raise
        except Exception as e:
            return await _fin("ERROR", sql_text, _error_summary(e))

    # 4. 防火墙初检(原 SQL)
    first = check(sql_text)
    if not first.passed:
        return await _fin("BLOCKED", sql_text, first.reason or "BLOCKED")

    # 5. data_scope 改写(系统调用豁免;CUSTOM/未知值 fail-closed);
    #    列探测走执行目标库连接(动态源探测集 {default_schema, public},与 search_path 对齐)
    applied = AppliedSQL(sql=sql_text)
    if user_id is not None:
        try:
            async with engine.connect() as probe_conn:
                applied = await applier.apply(
                    session,
                    probe_conn,
                    user_id=user_id,
                    tenant_schemas=[route_schema, "public"],
                    sql=sql_text,
                )
        except DataScopeUnsupported as e:
            return await _fin("BLOCKED", sql_text, f"DATA_SCOPE_UNSUPPORTED: {e.reason}")
        except Exception as e:
            # 编排段基础设施异常(池超时/控制库抖动):ERROR 落审计,不裸 500
            return await _fin("ERROR", sql_text, _error_summary(e))
        if applied.warning:
            log.warning("data_scope 降级: %s (trace_id=%s)", applied.warning, trace_id)

    # 6. 改写产物复检:防火墙 + 保留表拒读 + schema 边界(纵深防御,datascope spec 清单 10)
    second = check(applied.sql)
    if not second.passed:
        log.error("改写产物未过防火墙复检 trace_id=%s reason=%s", trace_id, second.reason)
        return await _fin("BLOCKED", applied.sql, f"POST_REWRITE: {second.reason}")
    reserved = check_reserved_tables(applied.sql)
    if not reserved.passed:
        return await _fin("BLOCKED", applied.sql, reserved.reason or "RESERVED_TABLE")
    scope_verdict = check_schema_scope(applied.sql, {route_schema, "public"})
    if not scope_verdict.passed:
        return await _fin("BLOCKED", applied.sql, scope_verdict.reason or "CROSS_SCHEMA")

    return _Prepared(
        tenant_id=tenant_id, engine=engine, route_schema=route_schema, applied=applied
    )


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

    # 7. 只读池执行:事务内 SET LOCAL search_path + 行数截断
    applied = prepared.applied
    quoted_schema = prepared.route_schema.replace('"', '""')
    try:
        async with prepared.engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text(f'SET LOCAL search_path TO "{quoted_schema}", public')  # noqa: S608  # 标识符双引号转义,非值拼接
                )
                result = await conn.execute(text(applied.sql), applied.params)
                columns = [str(c) for c in result.keys()]
                rows = [[_json_cell(v) for v in r] for r in result.fetchmany(limit)]
    except Exception as e:
        return await _finalize(
            SqlAuditWriter(),
            started,
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=prepared.tenant_id,
            datasource_key=datasource_key,
            question=question,
            verdict="ERROR",
            sql_text=applied.sql,
            reason=_error_summary(e),
        )

    elapsed = int((time.monotonic() - started) * 1000)
    await SqlAuditWriter().record(
        SqlAuditEntry(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=prepared.tenant_id,
            datasource_key=datasource_key,
            question=question,
            sql_text=applied.sql,
            verdict="PASS",
            row_count=len(rows),
            duration_ms=elapsed,
        )
    )
    return ExecResult(
        verdict="PASS",
        columns=columns,
        rows=rows,
        row_count=len(rows),
        duration_ms=elapsed,
    )


async def explain(  # noqa: PLR0913 —— 与 execute 同构平铺
    session: AsyncSession,
    *,
    trace_id: str,
    tenant_key: str,
    datasource_key: str,
    sql_text: str,
    user_id: str | None,
    question: str | None,
) -> ExplainResult:
    """执行计划(010,chat spec「EXPLAIN 执行计划」):防线与 execute 全同(_prepare
    共享),复检通过后由服务端拼 `EXPLAIN (FORMAT JSON)` 前缀——EXPLAIN 文本永不进
    防火墙 check()(无新攻击面),永不生成 ANALYZE(不真执行目标 SQL)。
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

    applied = prepared.applied
    explain_sql = f"EXPLAIN (FORMAT JSON) {applied.sql}"
    quoted_schema = prepared.route_schema.replace('"', '""')
    try:
        async with prepared.engine.connect() as conn:
            async with conn.begin():
                await conn.execute(
                    text(f'SET LOCAL search_path TO "{quoted_schema}", public')  # noqa: S608  # 标识符双引号转义,非值拼接
                )
                result = await conn.execute(text(explain_sql), applied.params)
                raw = result.scalar_one()
    except Exception as e:
        finalized = await _finalize(
            SqlAuditWriter(),
            started,
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=prepared.tenant_id,
            datasource_key=datasource_key,
            question=question,
            verdict="ERROR",
            sql_text=explain_sql,
            reason=_error_summary(e),
        )
        return ExplainResult(
            verdict="ERROR", reason=finalized.reason, duration_ms=finalized.duration_ms
        )

    # EXPLAIN (FORMAT JSON) 返回单行单列 json;asyncpg 对 json 列给 str,解析失败降级 raw
    plan: object
    if isinstance(raw, str):
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            plan = {"raw": raw}
    else:
        plan = raw

    elapsed = int((time.monotonic() - started) * 1000)
    await SqlAuditWriter().record(
        SqlAuditEntry(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=prepared.tenant_id,
            datasource_key=datasource_key,
            question=question,
            sql_text=explain_sql,  # EXPLAIN 形态落审计(可回放,与 PASS 口径一致)
            verdict="PASS",
            row_count=0,
            duration_ms=elapsed,
        )
    )
    return ExplainResult(verdict="PASS", plan=plan, duration_ms=elapsed)
