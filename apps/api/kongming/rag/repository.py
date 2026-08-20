"""rag 仓储(009):rag 两表 SQL + 租户解析 + 全量内省编排(RagRepo 端口实现)。

纪律:向量一律绑定参数 + CAST(:v AS vector) 强转(禁 f-string 拼接序列化向量);
tsvector 应用层维护(to_tsvector('simple', …),与 embedding 文本内容对齐);
召回查询强制 tenant + datasource 双过滤(rag spec「跨租户+跨数据源隔离」);
审计复用 auth 域 SqlOperLogRepo(同会话=同事务,007 组合模式)。
"""

import json

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.datasource.resolver import DatasourceInvalid, resolve_for_read
from kongming.db import uuid7
from kongming.internal.executor import get_or_create_engine
from kongming.rag.models import RECALL_TOP_K, IntrospectedTable, KnowledgeEntry, RecallHit
from kongming.sqlfw.firewall import RESERVED_TABLE_NAMES, RESERVED_TABLE_PREFIXES


def _vec_literal(vector: list[float]) -> str:
    """向量 → pgvector 字面量 '[v1,v2,…]'(绑定参数传输,驱动参数化)。"""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _is_reserved(table_name: str) -> bool:
    """与 internal/meta._is_reserved 同口径(控制面保留表不入知识库/内省)。"""
    name = table_name.lower()
    return name.startswith(RESERVED_TABLE_PREFIXES) or name in RESERVED_TABLE_NAMES


class RagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._oper = SqlOperLogRepo(session)  # 审计与业务同会话=同事务(007 组合模式)

    # ============ 事务 ============

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()

    async def log_oper(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None:
        await self._oper.add(user_id, action, target, detail)

    # ============ 写入:upsert / 删除 ============

    async def upsert_table(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        schema_name: str,
        table_name: str,
        table_comment: str,
        columns_comment: dict[str, str],
        sample_queries: list[str],
        source_type: str,
        embedding: list[float] | None,
    ) -> None:
        """四元幂等 upsert;embedding 为 None 时清空向量(不参与向量召回,全文路可用)。"""
        vec = _vec_literal(embedding) if embedding else None
        cols_text = " ".join(f"{k} {v}" for k, v in columns_comment.items())
        await self._s.execute(
            text(
                "INSERT INTO rag.table_descriptions"
                " (id, tenant_id, datasource_key, schema_name, table_name,"
                "  table_comment, columns_comment, sample_queries, source_type,"
                "  embedding, tsv, created_at, updated_at)"
                " VALUES (:id, :tenant, :ds, :schema, :table, :comment,"
                "  CAST(:cols AS jsonb), CAST(:samples AS jsonb), :source,"
                "  CAST(:vec AS vector),"
                "  to_tsvector('simple', :table_t || ' ' || :comment_t || ' ' || :cols_text),"
                "  now(), now())"
                " ON CONFLICT (tenant_id, datasource_key, schema_name, table_name)"
                " DO UPDATE SET table_comment = EXCLUDED.table_comment,"
                "  columns_comment = EXCLUDED.columns_comment,"
                "  sample_queries = EXCLUDED.sample_queries,"
                "  source_type = EXCLUDED.source_type,"
                "  embedding = EXCLUDED.embedding, tsv = EXCLUDED.tsv,"
                "  updated_at = now()"
            ),
            {
                "id": str(uuid7()),
                "tenant": tenant_id,
                "ds": datasource_key,
                "schema": schema_name,
                "table": table_name,
                "comment": table_comment,
                "cols": json.dumps(columns_comment, ensure_ascii=False),
                "samples": json.dumps(sample_queries, ensure_ascii=False),
                "source": source_type,
                "vec": vec,
                "cols_text": cols_text,
                # tsv 内容参数与列值参数分开命名(同值不同参):asyncpg 对同一参数
                # 在 varchar 列与 tsvector 输入两处推断类型会报 AmbiguousParameter
                "table_t": table_name,
                "comment_t": table_comment or " ",
            },
        )

    async def list_source_types(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, str]:
        """表名 → source_type(fetch-metadata 批量优先级判定,一次读)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT table_name, source_type FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).all()
        return {str(r.table_name): str(r.source_type) for r in rows}

    async def list_tables(
        self,
        *,
        tenant_id: str,
        datasource_key: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[KnowledgeEntry], int]:
        """管理面列表:updated_at 倒序(id tiebreak);不取向量/tsv 列。

        静态 SQL 两形态(datasource 筛选有/无),不做任何字符串拼接。"""
        if datasource_key is None:
            count_sql = "SELECT count(*) FROM rag.table_descriptions WHERE tenant_id = :t"
            list_sql = (
                "SELECT id, datasource_key, schema_name, table_name, table_comment,"
                " columns_comment, sample_queries, source_type, updated_at"
                " FROM rag.table_descriptions WHERE tenant_id = :t"
                " ORDER BY updated_at DESC, id DESC LIMIT :lim OFFSET :off"
            )
            params: dict[str, object] = {"t": tenant_id}
        else:
            count_sql = (
                "SELECT count(*) FROM rag.table_descriptions"
                " WHERE tenant_id = :t AND datasource_key = :d"
            )
            list_sql = (
                "SELECT id, datasource_key, schema_name, table_name, table_comment,"
                " columns_comment, sample_queries, source_type, updated_at"
                " FROM rag.table_descriptions"
                " WHERE tenant_id = :t AND datasource_key = :d"
                " ORDER BY updated_at DESC, id DESC LIMIT :lim OFFSET :off"
            )
            params = {"t": tenant_id, "d": datasource_key}
        total = (await self._s.execute(text(count_sql), params)).scalar_one()
        rows = (
            await self._s.execute(text(list_sql), {**params, "lim": limit, "off": offset})
        ).all()
        return [self._to_entry(r, tenant_id) for r in rows], int(total)

    def _to_entry(self, row: object, tenant_id: str) -> KnowledgeEntry:
        return KnowledgeEntry(
            id=str(row.id),  # type: ignore[attr-defined]
            tenant_id=tenant_id,
            datasource_key=str(row.datasource_key),  # type: ignore[attr-defined]
            schema_name=str(row.schema_name),  # type: ignore[attr-defined]
            table_name=str(row.table_name),  # type: ignore[attr-defined]
            table_comment=row.table_comment or "",  # type: ignore[attr-defined]
            columns_comment=dict(row.columns_comment or {}),  # type: ignore[attr-defined]
            sample_queries=list(row.sample_queries or []),  # type: ignore[attr-defined]
            source_type=str(row.source_type),  # type: ignore[attr-defined]
            updated_at=row.updated_at,  # type: ignore[attr-defined]
        )

    async def find_table(self, *, tenant_id: str, table_id: str) -> KnowledgeEntry | None:
        """单条取(租户归属限定;不命中=None→同形 404)。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT id, datasource_key, schema_name, table_name, table_comment,"
                    " columns_comment, sample_queries, source_type, updated_at"
                    " FROM rag.table_descriptions WHERE id = :id AND tenant_id = :t"
                ),
                {"id": table_id, "t": tenant_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return KnowledgeEntry(
            id=str(row["id"]),
            tenant_id=tenant_id,
            datasource_key=str(row["datasource_key"]),
            schema_name=str(row["schema_name"]),
            table_name=str(row["table_name"]),
            table_comment=row["table_comment"] or "",
            columns_comment=dict(row["columns_comment"] or {}),
            sample_queries=list(row["sample_queries"] or []),
            source_type=str(row["source_type"]),
            updated_at=row["updated_at"],
        )

    async def delete_table(self, *, tenant_id: str, table_id: str) -> bool:
        """硬删单条(RETURNING 计数,与 chat repository 同款)。"""
        rows = (
            await self._s.execute(
                text(
                    "DELETE FROM rag.table_descriptions WHERE id = :id AND tenant_id = :t"
                    " RETURNING id"
                ),
                {"id": table_id, "t": tenant_id},
            )
        ).all()
        return bool(rows)

    async def delete_all_for_datasource(self, *, tenant_id: str, datasource_key: str) -> int:
        """reindex 前清空该租户该数据源全部条目(显式重建语义)。"""
        rows = (
            await self._s.execute(
                text(
                    "DELETE FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " RETURNING id"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).all()
        return len(rows)

    # ============ 召回:三路(全部 tenant+ds 双过滤) ============

    async def recall_vector(
        self, *, tenant_id: str, datasource_key: str, vector: list[float]
    ) -> list[RecallHit]:
        """向量路:pgvector 余弦距离 <=>(topK)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT schema_name, table_name FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND embedding IS NOT NULL"
                    " ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :k"
                ),
                {
                    "t": tenant_id,
                    "d": datasource_key,
                    "vec": _vec_literal(vector),
                    "k": RECALL_TOP_K,
                },
            )
        ).all()
        return [RecallHit(schema_name=r.schema_name, table_name=r.table_name) for r in rows]

    async def recall_fulltext(
        self, *, tenant_id: str, datasource_key: str, question: str
    ) -> list[RecallHit]:
        """全文路:tsv @@ plainto_tsquery('simple',…),ts_rank 近似 BM25。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT schema_name, table_name FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND tsv @@ plainto_tsquery('simple', :q)"
                    " ORDER BY ts_rank(tsv, plainto_tsquery('simple', :q)) DESC"
                    " LIMIT :k"
                ),
                {"t": tenant_id, "d": datasource_key, "q": question, "k": RECALL_TOP_K},
            )
        ).all()
        return [RecallHit(schema_name=r.schema_name, table_name=r.table_name) for r in rows]

    async def recall_history(
        self, *, tenant_id: str, datasource_key: str, vector: list[float]
    ) -> list[str]:
        """历史路:问句向量相似,仅 SUCCESS;返回 sql_text(独立强 few-shot 通道)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT sql_text FROM rag.query_history"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND status = 'SUCCESS' AND embedding IS NOT NULL"
                    " ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :k"
                ),
                {
                    "t": tenant_id,
                    "d": datasource_key,
                    "vec": _vec_literal(vector),
                    "k": RECALL_TOP_K,
                },
            )
        ).all()
        return [str(r.sql_text) for r in rows]

    async def fetch_entries(
        self, *, tenant_id: str, datasource_key: str, keys: list[tuple[str, str]]
    ) -> list[KnowledgeEntry]:
        """按 (schema, table) 集取条目内容(四维限定,防同名表跨 schema 串扰)。"""
        out: list[KnowledgeEntry] = []
        for schema, table in keys:
            rows = (
                await self._s.execute(
                    text(
                        "SELECT id, datasource_key, schema_name, table_name, table_comment,"
                        " columns_comment, sample_queries, source_type, updated_at"
                        " FROM rag.table_descriptions"
                        " WHERE tenant_id = :t AND datasource_key = :d"
                        " AND schema_name = :s AND table_name = :n"
                    ),
                    {"t": tenant_id, "d": datasource_key, "s": schema, "n": table},
                )
            ).all()
            out.extend(self._to_entry(r, tenant_id) for r in rows)
        return out

    # ============ 回写 ============

    async def insert_history(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        embedding: list[float] | None,
    ) -> None:
        vec = _vec_literal(embedding) if embedding else None
        await self._s.execute(
            text(
                "INSERT INTO rag.query_history"
                " (id, tenant_id, datasource_key, question, sql_text, status,"
                "  embedding, created_at)"
                " VALUES (:id, :t, :d, :q, :sql, 'SUCCESS', CAST(:vec AS vector), now())"
            ),
            {
                "id": str(uuid7()),
                "t": tenant_id,
                "d": datasource_key,
                "q": question,
                "sql": sql_text,
                "vec": vec,
            },
        )

    # ============ 租户解析(与 chat/options 白名单复校同构) ============

    async def resolve_bound_tenant(
        self, user_id: str, tenant_key: str
    ) -> tuple[str, str] | None:
        repo = SqlDatasourceRepo(self._s)
        tenant_id = await repo.find_bound_tenant(user_id, tenant_key)
        return (tenant_id, tenant_key) if tenant_id is not None else None

    async def resolve_default_tenant(self, user_id: str) -> tuple[str, str] | None:
        return await SqlDatasourceRepo(self._s).find_default_tenant(user_id)

    async def tenant_id_by_key(self, tenant_key: str) -> str | None:
        """内部召回端点租户解析(与 meta 通道同语义:未知/禁用同形 404)。

        统一转 str:is_usable 的归属比对在 str 域(asyncpg 返回 UUID 曾致
        合法数据源被恒拒——冒烟实证,负向测试曾掩蔽)。
        """
        v = (
            await self._s.execute(
                text(
                    "SELECT id FROM public.sys_tenant"
                    " WHERE tenant_key = :k AND status = 'ACTIVE'"
                ),
                {"k": tenant_key},
            )
        ).scalar_one_or_none()
        return None if v is None else str(v)

    async def datasource_usable(self, datasource_key: str, tenant_id: str) -> bool:
        """存在+启用+归属(chat 预检同款 DatasourceChecker 语义)。"""
        return await SqlDatasourceRepo(self._s).is_usable(datasource_key, tenant_id)

    # ============ 全量内省(fetch-metadata;与 meta 通道同口径,无截断) ============

    async def introspect_all(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[IntrospectedTable]:
        """default=本库 [tenant_<key>, public];动态源=resolver 校验+动态池限
        [default_schema](业务库连接是基础设施职责,归仓储层)。"""
        if datasource_key == "default":
            return await _introspect_all_on(self._s, [f"tenant_{tenant_key}", "public"])
        try:
            row, plain = await resolve_for_read(
                self._s, tenant_id=tenant_id, datasource_key=datasource_key
            )
        except DatasourceInvalid:
            raise LookupError(datasource_key) from None
        engine = await get_or_create_engine(
            datasource_key,
            URL.create(
                "postgresql+asyncpg",
                username=row.username,
                password=plain,
                host=row.host,
                port=row.port,
                database=row.database,
            ),
        )
        async with engine.connect() as conn:
            return await _introspect_all_on(conn, [row.default_schema])


async def _introspect_all_on(
    conn: AsyncSession | AsyncConnection, schemas: list[str]
) -> list[IntrospectedTable]:
    """全量内省实现(信息模式查询与 internal/meta._introspect 同口径;无截断)。"""
    table_rows = (
        await conn.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_schema = ANY(CAST(:schemas AS text[]))"
                " AND table_type IN ('BASE TABLE', 'VIEW')"
                " ORDER BY table_schema, table_name"
            ),
            {"schemas": schemas},
        )
    ).all()
    kept = [r for r in table_rows if not _is_reserved(r.table_name)]
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
    return [
        IntrospectedTable(
            schema_name=r.table_schema,
            table_name=r.table_name,
            table_comment=comments.get((r.table_schema, r.table_name), ""),
            columns_comment=columns.get((r.table_schema, r.table_name), {}),
        )
        for r in kept
    ]
