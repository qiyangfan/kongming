"""知识库仓储的列与 schema 目录支撑:向量字面量、分词配置自检、全量内省。

与 RagRepository(repository.py)同属基础设施层;拆分后仓储只保留表数据读写。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from kongming.config import validated_fulltext_ts_config
from kongming.rag.models import IntrospectedTable
from kongming.sqlfw.firewall import RESERVED_TABLE_NAMES, RESERVED_TABLE_PREFIXES


def vec_literal(vector: list[float]) -> str:
    """向量 → pgvector 字面量 '[v1,v2,…]'(绑定参数传输,驱动参数化)。"""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


async def assert_fulltext_ts_config_available(session: AsyncSession, ts_config: str) -> str:
    """启动自检:分词配置目标必须在 pg_ts_config 存在,缺失 fail-closed 拒启。

    检索配置降级不安全(降级=换词法,与存量 tsv 直接错配),
    故 prod/dev 同拒启,无降级形态;文案明示缺项与安装配方指引。白名单校验
    先行(先词法后存在性,错误归因不混淆)。lifespan 启动期调用;测试直调。
    """
    cfg = validated_fulltext_ts_config(ts_config)
    hit = (
        await session.execute(
            text("SELECT 1 FROM pg_ts_config WHERE cfgname = :c LIMIT 1"),
            {"c": cfg},
        )
    ).scalar_one_or_none()
    if hit is None:
        raise RuntimeError(
            f"全文检索分词配置 {cfg} 在 pg_ts_config 中不存在(分词器未安装,"
            "如 zhparser 扩展缺失)——检索配置降级会与存量 tsv 错配,拒绝启动;"
            "启用配方见 docs/HANDOFF.md「中文分词(zhparser)启用配方」,"
            "或改配 KM_RAG_FULLTEXT_TS_CONFIG=simple 回退缺省"
        )
    return cfg


def is_reserved(table_name: str) -> bool:
    """与 internal/meta._is_reserved 同口径(控制面保留表不入知识库/内省)。"""
    name = table_name.lower()
    return name.startswith(RESERVED_TABLE_PREFIXES) or name in RESERVED_TABLE_NAMES


def allowed_schemas_for(tenant_key: str, default_schema: str | None) -> list[str]:
    """可扫描 schema 集合唯一计算点:default 源=路由约定
    [tenant_<key>, public](物理 schema 随租户首查创建,枚举先行于建表合法);
    动态源=[default_schema]。内省/枚举端点/上传校验三方共用,禁另起口径。"""
    if default_schema is None:
        return [f"tenant_{tenant_key}", "public"]
    return [default_schema]


async def introspect_all_on(
    conn: AsyncSession | AsyncConnection, schemas: list[str]
) -> list[IntrospectedTable]:
    """全量内省实现(信息模式查询与 internal/meta._introspect 同口径;无截断)。"""
    table_rows = (
        await conn.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_schema = ANY(CAST(:schemas AS text[]))"
                # 收录限基表——视图是基表派生物,行扇出使聚合失真,
                # 样例 SQL 挤占召回 top-K 使维度表缺位;
                # 基表可完整表达视图可表达的一切查询
                " AND table_type = 'BASE TABLE'"
                " ORDER BY table_schema, table_name"
            ),
            {"schemas": schemas},
        )
    ).all()
    kept = [r for r in table_rows if not is_reserved(r.table_name)]
    if not kept:
        return []
    col_rows = (
        await conn.execute(
            text(
                "SELECT c.table_schema, c.table_name, c.column_name, c.data_type"
                " FROM information_schema.columns c"
                " WHERE c.table_schema = ANY(CAST(:schemas AS text[]))"
                " ORDER BY c.table_schema, c.table_name, c.ordinal_position"
            ),
            {"schemas": schemas},
        )
    ).all()
    columns: dict[tuple[str, str], dict[str, str]] = {}
    for r in col_rows:
        columns.setdefault((r.table_schema, r.table_name), {})[r.column_name] = r.data_type
    comment_rows = (
        await conn.execute(
            text(
                "SELECT c.table_schema, c.table_name,"
                " obj_description(cls.oid, 'pg_class') AS table_comment"
                " FROM information_schema.tables c"
                " JOIN pg_class cls ON cls.relname = c.table_name"
                " JOIN pg_namespace n ON n.oid = cls.relnamespace"
                "   AND n.nspname = c.table_schema"
                " WHERE c.table_schema = ANY(CAST(:schemas AS text[]))"
            ),
            {"schemas": schemas},
        )
    ).all()
    comments = {(r.table_schema, r.table_name): r.table_comment or "" for r in comment_rows}
    # 列注释取数据库既有 col_description:有注释列以其为说明,
    # 无注释列保持英文类型形态(类型即说明)
    col_comment_rows = (
        await conn.execute(
            text(
                "SELECT c.table_schema, c.table_name, c.column_name,"
                " col_description(cls.oid, a.attnum) AS column_comment"
                " FROM information_schema.columns c"
                " JOIN pg_class cls ON cls.relname = c.table_name"
                " JOIN pg_namespace n ON n.oid = cls.relnamespace"
                "   AND n.nspname = c.table_schema"
                " JOIN pg_attribute a ON a.attrelid = cls.oid AND a.attname = c.column_name"
                " WHERE c.table_schema = ANY(CAST(:schemas AS text[]))"
            ),
            {"schemas": schemas},
        )
    ).all()
    col_comments = {
        (r.table_schema, r.table_name, r.column_name): r.column_comment or ""
        for r in col_comment_rows
    }
    return [
        IntrospectedTable(
            schema_name=r.table_schema,
            table_name=r.table_name,
            table_comment=comments.get((r.table_schema, r.table_name), ""),
            columns_comment={
                name: col_comments.get((r.table_schema, r.table_name, name)) or dtype
                for name, dtype in columns.get((r.table_schema, r.table_name), {}).items()
            },
        )
        for r in kept
    ]
