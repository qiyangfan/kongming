"""rag 仓储(009):rag 两表 SQL + 租户解析 + 全量内省编排(RagRepo 端口实现)。

纪律:向量一律绑定参数 + CAST(:v AS vector) 强转(禁 f-string 拼接序列化向量);
tsvector 应用层维护(to_tsvector('simple', …),与 embedding 文本内容对齐);
召回查询强制 tenant + datasource 双过滤(rag spec「跨租户+跨数据源隔离」);
审计复用 auth 域 SqlOperLogRepo(同会话=同事务,007 组合模式)。
"""

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.datasource.resolver import DatasourceInvalid, resolve_for_read
from kongming.db import uuid7
from kongming.internal.executor import get_or_create_engine
from kongming.rag.models import (
    AUTOMATED_SOURCE_TYPES,
    COVERAGE_FAILURE_ROW_LIMIT,
    QUERY_HISTORY_CAP,
    RECALL_TOP_K,
    FailureSignal,
    HistoryOverview,
    IntrospectedTable,
    KnowledgeEntry,
    RecallHit,
)
from kongming.rag.models import (
    DatasourceInvalid as RagDatasourceInvalid,
)
from kongming.sqlfw.firewall import RESERVED_TABLE_NAMES, RESERVED_TABLE_PREFIXES


def vec_literal(vector: list[float]) -> str:
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
        alias: str = "",  # 037:业务称谓别名
    ) -> None:
        """四元幂等 upsert;embedding 为 None 时自动来源清空向量(009 降级语义)。

        037 alias 覆盖规则(knowledge spec「补齐不改写既有非空 alias」):
        自动来源(INTROSPECTION/AI_GENERATED)与上传(MANUAL_UPLOAD——parser 无
        别名语法,清空非显式意图)只补空 alias,不改写既有非空值;人工精修
        (MANUAL_EDIT/FEEDBACK_REFINED)显式操作即显式覆盖。
        **tsv 同规则同源**(R1-S1):alias 命中保留分支时,tsv 须以「生效别名」
        (=既有别名)在 DO UPDATE 内重算,否则派生检索文本与 alias 列不同源,
        别名召回静默失效;人工精修路径 embedding 传 None 时保留既有向量
        (清向量会使该表退出向量召回)。
        """
        vec = vec_literal(embedding) if embedding else None
        cols_text = " ".join(f"{k} {v}" for k, v in columns_comment.items())
        # 生效别名(单一定义):保留分支命中→既有别名;否则→传入别名。
        # alias 列、tsv、tsv 参数四处引用保持一致(EXCLUDED.alias=:alias 值)。
        effective_alias = (
            "COALESCE(CASE WHEN :source IN ('INTROSPECTION', 'AI_GENERATED',"
            " 'MANUAL_UPLOAD') AND COALESCE(rag.table_descriptions.alias, '') <> ''"
            " THEN rag.table_descriptions.alias END, EXCLUDED.alias)"
        )
        await self._s.execute(
            text(
                # S608 豁免:effective_alias 为本文件内常量片段(非用户输入),
                # 其余全部为绑定参数——无任何外部字符串拼接
                "INSERT INTO rag.table_descriptions"  # noqa: S608
                " (id, tenant_id, datasource_key, schema_name, table_name,"
                "  table_comment, columns_comment, sample_queries, source_type,"
                "  embedding, tsv, alias, created_at, updated_at)"
                " VALUES (:id, :tenant, :ds, :schema, :table, :comment,"
                "  CAST(:cols AS jsonb), CAST(:samples AS jsonb), :source,"
                "  CAST(:vec AS vector),"
                "  to_tsvector('simple', :table_t || ' ' || :alias_t || ' ' ||"
                "  :comment_t || ' ' || :cols_text),"
                "  :alias, now(), now())"
                " ON CONFLICT (tenant_id, datasource_key, schema_name, table_name)"
                " DO UPDATE SET table_comment = EXCLUDED.table_comment,"
                "  columns_comment = EXCLUDED.columns_comment,"
                "  sample_queries = EXCLUDED.sample_queries,"
                "  source_type = EXCLUDED.source_type,"
                "  embedding = CASE WHEN :source IN ('INTROSPECTION', 'AI_GENERATED')"
                "   THEN EXCLUDED.embedding"
                "   ELSE COALESCE(EXCLUDED.embedding, rag.table_descriptions.embedding) END,"
                "  tsv = to_tsvector('simple', :table_t || ' ' ||"
                f"  {effective_alias} || ' ' || :comment_t || ' ' || :cols_text),"
                "  alias = " + effective_alias + ","
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
                "alias": alias,
                # tsv 内容参数与列值参数分开命名(同值不同参):asyncpg 对同一参数
                # 在 varchar 列与 tsvector 输入两处推断类型会报 AmbiguousParameter
                "table_t": table_name,
                "alias_t": alias or " ",  # 037:alias 进 tsv
                "comment_t": table_comment or " ",
            },
        )

    async def list_source_and_alias(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[tuple[str, str], tuple[str, str]]:
        """(schema_name, table_name) → (source_type, alias)——优先级判定与生效
        别名(R1-S1:embedding 文本须以生效别名参与,与 alias 列/tsv 同源),
        一次读免二次往返;042 二元键:default 源合法范围含两个 schema
        (tenant_<key> + public),一元键会让跨 schema 同名表互遮。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT schema_name, table_name, source_type,"
                    " COALESCE(alias, '') AS alias"
                    " FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).all()
        return {
            (str(r.schema_name), str(r.table_name)): (str(r.source_type), str(r.alias or ""))
            for r in rows
        }

    async def delete_stale_entries(
        self, *, tenant_id: str, datasource_key: str, keep: list[tuple[str, str]]
    ) -> tuple[int, int]:
        """042 对账(登记=现实):删扫描集外自动来源行,人工/反馈行只计数不删
        (不可逆资产交人工裁决,service 层告警)。keep 空集=该范围已无表,全删
        自动行;静态 SQL 双形态(keep 有/无),禁字符串拼 keep 集。"""
        sources = sorted(AUTOMATED_SOURCE_TYPES)
        base = (
            "FROM rag.table_descriptions"
            " WHERE tenant_id = :t AND datasource_key = :d"
            " AND source_type = ANY(CAST(:sources AS text[]))"
        )
        params: dict[str, object] = {"t": tenant_id, "d": datasource_key, "sources": sources}
        if keep:
            base += (
                " AND NOT EXISTS (SELECT 1 FROM unnest("
                " CAST(:schemas AS text[]), CAST(:tables AS text[])) AS k(s, n)"
                " WHERE k.s = schema_name AND k.n = table_name)"
            )
            params["schemas"] = [s for s, _ in keep]
            params["tables"] = [t for _, t in keep]
        orphans = int(
            (
                await self._s.execute(text(f"SELECT count(*) {base}"), params)  # noqa: S608  # base 为本文件常量片段+绑定参数
            ).scalar_one()
        )
        deleted = (await self._s.execute(text(f"DELETE {base} RETURNING 1"), params)).all()  # noqa: S608
        return len(deleted), orphans

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]:
        """表名 → 列注释(041 呈现层列标签)。注释原文含 PG 类型前缀
        (fetch-metadata 形态,类型段同时是 LLM 生成输入),剥离在 chat
        呈现纯函数内,此处不丢信息。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT table_name, columns_comment"
                    " FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " ORDER BY CASE WHEN schema_name = 'public' THEN 0 ELSE 1 END"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).all()
        # 同名表跨 schema(default 源合法并存 tenant_<key> 与 public)时
        # 字典后写覆盖:排序保证 public 先读、租户 schema 后写胜出——
        # 标签取「本租户」注释,方向确定(R1 实锤:无序覆盖方向随计划漂移)
        return {str(r.table_name): dict(r.columns_comment or {}) for r in rows}

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
                " columns_comment, sample_queries, source_type, updated_at,"
                " COALESCE(alias, '') AS alias"
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
                " columns_comment, sample_queries, source_type, updated_at,"
                " COALESCE(alias, '') AS alias"
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
            alias=str(row.alias or ""),  # type: ignore[attr-defined]  # 037
        )

    async def find_table(self, *, tenant_id: str, table_id: str) -> KnowledgeEntry | None:
        """单条取(租户归属限定;不命中=None→同形 404)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, datasource_key, schema_name, table_name, table_comment,"
                        " columns_comment, sample_queries, source_type, updated_at,"
                        " COALESCE(alias, '') AS alias"
                        " FROM rag.table_descriptions WHERE id = :id AND tenant_id = :t"
                    ),
                    {"id": table_id, "t": tenant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
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
            alias=str(row.get("alias") or ""),  # 037
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
                    "vec": vec_literal(vector),
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
                    "vec": vec_literal(vector),
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
                        " columns_comment, sample_queries, source_type, updated_at,"
                        " COALESCE(alias, '') AS alias"
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
        """幂等回写(016 治理):同 (tenant, ds, question) 覆盖保留最新成功 SQL
        (0007 唯一索引支撑);embedding 失败传 None 时保留旧向量(旧向量语义
        仍有效,清空会使该行退出向量召回);同事务上限淘汰该维度最旧行。
        """
        vec = vec_literal(embedding) if embedding else None
        await self._s.execute(
            text(
                "INSERT INTO rag.query_history"
                " (id, tenant_id, datasource_key, question, sql_text, status,"
                "  embedding, created_at)"
                " VALUES (:id, :t, :d, :q, :sql, 'SUCCESS', CAST(:vec AS vector), now())"
                " ON CONFLICT (tenant_id, datasource_key, md5(question)) DO UPDATE SET"
                "  sql_text = EXCLUDED.sql_text,"
                "  embedding = COALESCE(EXCLUDED.embedding, query_history.embedding),"
                "  status = 'SUCCESS', created_at = now()"
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
        # 上限淘汰:保留该维度 created_at 最新的 cap 行(id 作并列 tiebreak)
        cap = QUERY_HISTORY_CAP
        await self._s.execute(
            text(
                "DELETE FROM rag.query_history"
                " WHERE tenant_id = :t AND datasource_key = :d"
                " AND id NOT IN ("
                "   SELECT id FROM rag.query_history"
                "   WHERE tenant_id = :t AND datasource_key = :d"
                "   ORDER BY created_at DESC, id DESC LIMIT :cap)"
            ),
            {"t": tenant_id, "d": datasource_key, "cap": cap},
        )

    # ============ 租户解析(与 chat/options 白名单复校同构) ============

    async def resolve_bound_tenant(self, user_id: str, tenant_key: str) -> tuple[str, str] | None:
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
                    "SELECT id FROM public.sys_tenant WHERE tenant_key = :k AND status = 'ACTIVE'"
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
            return await _introspect_all_on(self._s, allowed_schemas_for(tenant_key, None))
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
            return await _introspect_all_on(
                conn, allowed_schemas_for(tenant_key, row.default_schema)
            )

    async def allowed_schemas(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[str]:
        """可扫描 schema 集合(040:枚举端点/上传存在性闸,与内省同源同口径)。

        动态源经 resolver 校验;resolver 的 DatasourceInvalid(resolver 模块
        自有同名类,与 rag 领域错误非一类)在此翻译为 rag 领域错误出港,
        调用方只认域错误——与 introspect_all 的 LookupError 翻译同哲学。
        """
        if datasource_key == "default":
            return allowed_schemas_for(tenant_key, None)
        try:
            row, _plain = await resolve_for_read(
                self._s, tenant_id=tenant_id, datasource_key=datasource_key
            )
        except DatasourceInvalid:
            raise RagDatasourceInvalid(datasource_key) from None
        return allowed_schemas_for(tenant_key, row.default_schema)

    async def knowledge_table_exists(
        self, *, tenant_id: str, datasource_key: str, schema_table: str
    ) -> bool:
        """知识条目表存在性(040:semantic 域手工关系闸经端口消费)。

        schema_table 形态「schema.table」(rsplit 拆解——表名不含点,
        PG 未加引号标识符语义);形态不合法即不存在。"""
        schema_name, sep, table_name = schema_table.rpartition(".")
        if not sep or not schema_name or not table_name:
            return False
        row = (
            await self._s.execute(
                text(
                    "SELECT 1 FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND schema_name = :sn AND table_name = :tn LIMIT 1"
                ),
                {"t": tenant_id, "d": datasource_key, "sn": schema_name, "tn": table_name},
            )
        ).scalar_one_or_none()
        return row is not None

    # ============ 052 知识覆盖报告(只读聚合输入;零写入零 LLM) ============

    async def list_all_entries(
        self, *, tenant_id: str, datasource_key: str
    ) -> list[KnowledgeEntry]:
        """该 (租户, 数据源) 全量登记条目(与 fetch_entries 同投影,无分页)。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, datasource_key, schema_name, table_name, table_comment,"
                    " columns_comment, sample_queries, source_type, updated_at,"
                    " COALESCE(alias, '') AS alias"
                    " FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " ORDER BY schema_name, table_name"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).all()
        return [self._to_entry(r, tenant_id) for r in rows]

    async def failure_signals(
        self, *, tenant_id: str, datasource_key: str, since: datetime
    ) -> list[FailureSignal]:
        """失败信号投影(chat_message 只读跨域,feedback 域同表先例)。

        口径:status ∈ {FAILED, REJECTED, NO_DATA} ∪ rating=DOWN ∪
        corrected_sql 非空;NO_DATA(如实弃答)作为知识未接住的弱信号计入,
        状态分列呈现。窗口 since 之外不收;行数受应用层上限约束
        (COVERAGE_FAILURE_ROW_LIMIT),归并在领域纯函数内。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT m.question, m.status, COALESCE(m.rating, '') AS rating,"
                    " (m.corrected_sql IS NOT NULL) AS has_corrected, m.created_at"
                    " FROM public.chat_message m"
                    " JOIN public.chat_session s ON s.id = m.session_id"
                    " WHERE s.tenant_id = :t AND s.datasource_key = :d"
                    " AND m.role = 'ASSISTANT' AND m.question IS NOT NULL"
                    " AND m.created_at >= CAST(:since AS timestamptz)"
                    " AND (m.status IN ('FAILED', 'REJECTED', 'NO_DATA')"
                    "      OR m.rating = 'DOWN' OR m.corrected_sql IS NOT NULL)"
                    " ORDER BY m.created_at DESC, m.id DESC"
                    " LIMIT :lim"
                ),
                {
                    "t": tenant_id,
                    "d": datasource_key,
                    "since": since,
                    "lim": COVERAGE_FAILURE_ROW_LIMIT,
                },
            )
        ).all()
        return [
            FailureSignal(
                question=str(r.question),
                status=str(r.status),
                rating=str(r.rating),
                has_corrected_sql=bool(r.has_corrected),
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def history_overview(self, *, tenant_id: str, datasource_key: str) -> HistoryOverview:
        """few-shot 通道概况:仅 SUCCESS(召回同口径);计数一次聚合完成。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT COUNT(*) AS n, COUNT(DISTINCT question) AS q"
                    " FROM rag.query_history"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND status = 'SUCCESS'"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).one()
        return HistoryOverview(
            success_count=int(row.n),
            distinct_questions=int(row.q),
            cap=QUERY_HISTORY_CAP,
        )


def allowed_schemas_for(tenant_key: str, default_schema: str | None) -> list[str]:
    """可扫描 schema 集合唯一计算点(040):default 源=路由约定
    [tenant_<key>, public](物理 schema 随租户首查创建,枚举先行于建表合法);
    动态源=[default_schema]。内省/枚举端点/上传校验三方共用,禁另起口径。"""
    if default_schema is None:
        return [f"tenant_{tenant_key}", "public"]
    return [default_schema]


async def _introspect_all_on(
    conn: AsyncSession | AsyncConnection, schemas: list[str]
) -> list[IntrospectedTable]:
    """全量内省实现(信息模式查询与 internal/meta._introspect 同口径;无截断)。"""
    table_rows = (
        await conn.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_schema = ANY(CAST(:schemas AS text[]))"
                # 047:收录限基表——视图是基表派生物,行扇出使聚合失真
                # (film_list AVG 实证),样例 SQL 挤占召回 top-K 使维度表
                # 缺位;基表可完整表达视图可表达的一切查询
                " AND table_type = 'BASE TABLE'"
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
    # 列注释(014 挂账⑦清零):数据库既有 col_description 作为润色输入的既有注释
    # 参考;有注释列以其为说明,无注释列保持英文类型形态(行为逐字不变)
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
