"""知识库仓储实现:条目读写、三路召回、样本回写与租户解析。

向量查询使用绑定参数，全文分词配置由 `KM_RAG_FULLTEXT_TS_CONFIG` 指定；
召回查询同时过滤租户和数据源。操作审计复用认证域仓储，并与当前数据库会话
处于同一事务。覆盖报告的只读查询见 coverage_repository;向量字面量、
分词自检与全量内省见 catalog。
"""

import json
from collections.abc import Sequence
from typing import ClassVar

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.config import DEFAULT_FULLTEXT_TS_CONFIG, validated_fulltext_ts_config
from kongming.datasource.repository import SqlDatasourceRepo
from kongming.datasource.resolver import DatasourceInvalid, resolve_for_read
from kongming.db import uuid7
from kongming.internal.executor import get_or_create_engine
from kongming.rag.catalog import allowed_schemas_for, introspect_all_on, vec_literal
from kongming.rag.coverage_repository import RagCoverageRepository
from kongming.rag.models import (
    AUTOMATED_SOURCE_TYPES,
    QUERY_HISTORY_CAP,
    RECALL_TOP_K,
    HistorySample,
    IntrospectedTable,
    KnowledgeEntry,
    RecallHit,
)
from kongming.rag.models import (
    DatasourceInvalid as RagDatasourceInvalid,
)
from kongming.rag.ports import (
    CoverageRepo,
    RagEntryRepo,
    RagRecallRepo,
    RagSampleRepo,
    RagTenantRepo,
)
from kongming.rag.row_mapping import to_entry

# 生效别名(单一定义):保留分支命中→既有别名;否则→传入别名。
# alias 列、tsv、tsv 参数四处引用保持一致(EXCLUDED.alias=:alias 值)。
_EFFECTIVE_ALIAS_SQL = (
    "COALESCE(CASE WHEN :source IN ('INTROSPECTION', 'AI_GENERATED',"
    " 'MANUAL_UPLOAD') AND COALESCE(rag.table_descriptions.alias, '') <> ''"
    " THEN rag.table_descriptions.alias END, EXCLUDED.alias)"
)


class RagRepository:
    """知识库主仓储:组合写入/事务、条目与召回、样本反馈、租户解析各窄类。

    全部成员共用同一数据库会话与分词配置;本类提供共享状态与事务收口,
    用例按需经属性访问对应子仓储(同一实例,事务语义与单类等价)。"""

    def __init__(self, session: AsyncSession, ts_config: str = DEFAULT_FULLTEXT_TS_CONFIG) -> None:
        self._s = session
        # 全文分词配置(白名单校验 fail-fast,组合根注入部署值,缺省 simple)。
        # 写(upsert tsv)与读(recall_fulltext)共用同一注入值——
        # 同库不分词两态,写读异配置 = 检索静默错配
        self._ts = validated_fulltext_ts_config(ts_config)
        self._oper = SqlOperLogRepo(session)  # 审计与业务同会话=同事务(组合模式)
        self.entries: RagEntryRepo = RagEntryRepository(self)
        self.recall: RagRecallRepo = RagRecallRepository(self)
        self.samples: RagSampleRepo = RagSampleRepository(self)
        self.tenant: RagTenantRepo = RagTenantRepository(self)
        self.coverage: CoverageRepo = RagCoverageRepository(session)

    @property
    def session(self) -> AsyncSession:
        """共享会话(子仓储构造用,事务语义与主仓储一致)。"""
        return self._s

    @property
    def ts_config(self) -> str:
        """全文分词配置(子仓储读写共用,避免写读异配)。"""
        return self._ts

    # ============ 事务(组合根收口,供各用例共用) ============

    async def commit(self) -> None:
        await self._s.commit()

    async def rollback(self) -> None:
        await self._s.rollback()

    async def log_oper(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None:
        await self._oper.add(user_id, action, target, detail)


class RagEntryRepository:
    """条目与租户绑定读写(写入幂等、列表、删除、归属变更)。"""

    def __init__(self, core: RagRepository) -> None:
        self._s = core.session
        self._ts = core.ts_config

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
        alias: str = "",  # 业务称谓别名
        team_id: str | None = None,  # 归属值(set_team_id=True 时生效)
        set_team_id: bool = False,  # False=保留既有归属(缺省)
    ) -> None:
        """按租户、数据源、schema 和表名幂等写入。自动来源和文件上传只补空 alias；
        人工精修可覆盖，tsv 使用最终生效的 alias。embedding 为 None 时，人工精修保留
        原向量，自动来源清空原向量。上传未指定 team_id 时保留现有归属；自动扫描将
        机器生成条目设为公共。
        """
        vec = vec_literal(embedding) if embedding else None
        cols_text = " ".join(f"{k} {v}" for k, v in columns_comment.items())
        await self._s.execute(
            text(
                # S608 豁免:_EFFECTIVE_ALIAS_SQL 为本文件内常量片段(非用户输入),
                # 其余全部为绑定参数——无任何外部字符串拼接;{self._ts} 为
                # 白名单校验后的配置名(标识符无法绑定参数,单源注入)
                "INSERT INTO rag.table_descriptions"  # noqa: S608
                " (id, tenant_id, datasource_key, schema_name, table_name,"
                "  table_comment, columns_comment, sample_queries, source_type,"
                "  embedding, tsv, alias, team_id, created_at, updated_at)"
                " VALUES (:id, :tenant, :ds, :schema, :table, :comment,"
                "  CAST(:cols AS jsonb), CAST(:samples AS jsonb), :source,"
                "  CAST(:vec AS halfvec),"
                f"  to_tsvector('{self._ts}', :table_t || ' ' || :alias_t || ' ' ||"
                "  :comment_t || ' ' || :cols_text),"
                "  :alias, CAST(:team AS uuid), now(), now())"
                " ON CONFLICT (tenant_id, datasource_key, schema_name, table_name)"
                " DO UPDATE SET table_comment = EXCLUDED.table_comment,"
                "  columns_comment = EXCLUDED.columns_comment,"
                "  sample_queries = EXCLUDED.sample_queries,"
                "  source_type = EXCLUDED.source_type,"
                "  embedding = CASE WHEN :source IN ('INTROSPECTION', 'AI_GENERATED')"
                "   THEN EXCLUDED.embedding"
                "   ELSE COALESCE(EXCLUDED.embedding, rag.table_descriptions.embedding) END,"
                f"  tsv = to_tsvector('{self._ts}', :table_t || ' ' ||"
                f"  {_EFFECTIVE_ALIAS_SQL} || ' ' || :comment_t || ' ' || :cols_text),"
                "  alias = " + _EFFECTIVE_ALIAS_SQL + ","
                "  team_id = CASE WHEN :team_set THEN CAST(:team AS uuid)"
                "   ELSE rag.table_descriptions.team_id END,"
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
                "team": team_id,
                "team_set": set_team_id,
                # tsv 内容参数与列值参数分开命名(同值不同参):asyncpg 对同一参数
                # 在 varchar 列与 tsvector 输入两处推断类型会报 AmbiguousParameter
                "table_t": table_name,
                "alias_t": alias or " ",  # alias 进 tsv
                "comment_t": table_comment or " ",
            },
        )

    async def list_source_and_alias(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[tuple[str, str], tuple[str, str, str | None]]:
        """(schema_name, table_name) → (source_type, alias, team_id)——优先级判定
        与生效别名(embedding 文本须以生效别名参与,与 alias 列/tsv 同源),
        一次读免二次往返;team_id 供上传写闸判既有条目归属。二元键:
        default 源合法范围含两个 schema(tenant_<key> + public),一元键会让跨
        schema 同名表互遮。"""
        rows = (
            await self._s.execute(
                text(
                    "SELECT schema_name, table_name, source_type,"
                    " COALESCE(alias, '') AS alias, team_id"
                    " FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).all()
        return {
            (str(r.schema_name), str(r.table_name)): (
                str(r.source_type),
                str(r.alias or ""),
                str(r.team_id) if r.team_id is not None else None,
            )
            for r in rows
        }

    async def delete_stale_entries(
        self, *, tenant_id: str, datasource_key: str, keep: list[tuple[str, str]]
    ) -> tuple[int, int]:
        """对账(登记=现实):删扫描集外自动来源行,人工/反馈行只计数不删
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
                await self._s.execute(
                    text(f"SELECT count(*) {base}"), params
                )  # base 为本文件常量片段+绑定参数
            ).scalar_one()
        )
        deleted = (await self._s.execute(text(f"DELETE {base} RETURNING 1"), params)).all()
        return len(deleted), orphans

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]:
        """表名 → 列注释(呈现层列标签)。注释原文含 PG 类型前缀
        (类型段同时是 LLM 生成输入),剥离在 chat 呈现纯函数内,此处不丢信息。"""
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
        # 租户 schema 排在 public 后，使租户注释覆盖同名表的公共注释。
        return {str(r.table_name): dict(r.columns_comment or {}) for r in rows}

    # 可见集合子句单源(scope 枚举→常量片段;teams 恒绑定参数,无拼接输入)
    _TEAM_CLAUSE: ClassVar[dict[str, str]] = {
        "all": "(rd.team_id IS NULL OR rd.team_id::text = ANY(CAST(:teams AS text[])))",
        "public": "rd.team_id IS NULL",
        "team": "(rd.team_id::text = :scope_team"
        " AND rd.team_id::text = ANY(CAST(:teams AS text[])))",
    }

    async def list_tables(
        self,
        *,
        tenant_id: str,
        datasource_key: str | None,
        limit: int,
        offset: int,
        team_scope: str = "all",
        visible_team_ids: Sequence[str] = (),
    ) -> tuple[list[KnowledgeEntry], int]:
        """管理面列表:updated_at 倒序(id tiebreak);不取向量/tsv 列。

        静态 SQL 两形态(datasource 筛选有/无),不做任何字符串拼接。
        可见集合=公共 ∪ 调用者团队(teams 绑定参数);team_scope ∈
        {all, public, <team_id>} 为管理面归属筛选(未知值防御性收窄为
        team 子句=空集——宁窄勿宽;路由 pattern 已保证正常形态)。
        """
        clause = (
            self._TEAM_CLAUSE[team_scope]
            if team_scope in self._TEAM_CLAUSE
            else self._TEAM_CLAUSE["team"]  # 其余=team id(路由 pattern 已保证形态)
        )
        params: dict[str, object] = {
            "t": tenant_id,
            "teams": list(visible_team_ids),
            "scope_team": team_scope,
        }
        ds_clause = ""
        if datasource_key is not None:
            ds_clause = " AND rd.datasource_key = :d"
            params["d"] = datasource_key
        count_sql = (
            "SELECT count(*) FROM rag.table_descriptions rd"  # noqa: S608  # 常量片段+绑定参数
            " WHERE rd.tenant_id = :t" + ds_clause + " AND " + clause
        )
        list_sql = (
            "SELECT rd.id, rd.datasource_key, rd.schema_name, rd.table_name,"  # noqa: S608  # 同上
            " rd.table_comment, rd.columns_comment, rd.sample_queries, rd.source_type,"
            " rd.updated_at, COALESCE(rd.alias, '') AS alias,"
            " rd.team_id::text AS team_id, st.name AS team_name"
            " FROM rag.table_descriptions rd"
            " LEFT JOIN public.sys_team st ON st.id = rd.team_id"
            " WHERE rd.tenant_id = :t"
            + ds_clause
            + " AND "
            + clause
            + " ORDER BY rd.updated_at DESC, rd.id DESC LIMIT :lim OFFSET :off"
        )
        total = (await self._s.execute(text(count_sql), params)).scalar_one()
        rows = (
            await self._s.execute(text(list_sql), {**params, "lim": limit, "off": offset})
        ).all()
        return [to_entry(r, tenant_id) for r in rows], int(total)

    async def find_table(self, *, tenant_id: str, table_id: str) -> KnowledgeEntry | None:
        """单条取(租户归属限定;不命中=None→同形 404)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT rd.id, rd.datasource_key, rd.schema_name, rd.table_name,"
                        " rd.table_comment, rd.columns_comment, rd.sample_queries,"
                        " rd.source_type, rd.updated_at, COALESCE(rd.alias, '') AS alias,"
                        " rd.team_id::text AS team_id, st.name AS team_name"
                        " FROM rag.table_descriptions rd"
                        " LEFT JOIN public.sys_team st ON st.id = rd.team_id"
                        " WHERE rd.id = CAST(:id AS uuid) AND rd.tenant_id = CAST(:t AS uuid)"
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
            alias=str(row.get("alias") or ""),
            team_id=row["team_id"],
            team_name=row["team_name"],
        )

    async def set_entry_team(self, *, tenant_id: str, table_id: str, team_id: str | None) -> bool:
        """归属变更原位置 NULL⇄team_id(单一实例,内容与来源标记不动)。"""
        rows = (
            await self._s.execute(
                text(
                    "UPDATE rag.table_descriptions SET team_id = CAST(:team AS uuid)"
                    " WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:t AS uuid)"
                    " RETURNING id"
                ),
                {"id": table_id, "t": tenant_id, "team": team_id},
            )
        ).all()
        return bool(rows)

    async def publicize_team_entries(self, *, team_id: str) -> list[tuple[str, str]]:
        """团队删除钩子:名下条目归属置空(公共化,知识资产不丢失),
        返回受影响 (tenant_id, datasource_key) 去重范围(供调用方精确失效
        检索缓存)。团队行本身的删除由调用方随后执行;FK ON DELETE SET NULL
        为钩子失败时的级联兜底(同语义公共化,无审计)。"""
        rows = (
            await self._s.execute(
                text(
                    "UPDATE rag.table_descriptions SET team_id = NULL"
                    " WHERE team_id = CAST(:t AS uuid)"
                    " RETURNING tenant_id::text AS tenant_id, datasource_key"
                ),
                {"t": team_id},
            )
        ).all()
        return sorted({(str(r.tenant_id), str(r.datasource_key)) for r in rows})

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


class RagRecallRepository:
    """三路召回与条目取数(全部 tenant+ds 双过滤,叠加可见集合)。"""

    def __init__(self, core: RagRepository) -> None:
        self._s = core.session
        self._ts = core.ts_config

    # ============ 召回:三路(全部 tenant+ds 双过滤) ============

    async def recall_vector(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        vector: list[float],
        visible_team_ids: Sequence[str] = (),
    ) -> list[RecallHit]:
        """向量路:pgvector 余弦距离 <=>(topK)。

        SELECT 回原生分 `1-(embedding<=>v)`(余弦相似度,值域约 [0,1];
        表达式仅算一次并同时用于排序与返回——ORDER BY 子查询消重复计算)。
        可见集合叠加(tenant+ds 双过滤之上的条目级过滤)——公共条目 ∪
        调用者团队条目;空集合=纯公共形态(存量行全 NULL)。
        """
        rows = (
            await self._s.execute(
                text(
                    "SELECT schema_name, table_name,"
                    " 1 - (embedding <=> CAST(:vec AS halfvec)) AS score"
                    " FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND embedding IS NOT NULL"
                    " AND (team_id IS NULL OR team_id::text = ANY(CAST(:teams AS text[])))"
                    " ORDER BY embedding <=> CAST(:vec AS halfvec) LIMIT :k"
                ),
                {
                    "t": tenant_id,
                    "d": datasource_key,
                    "vec": vec_literal(vector),
                    "k": RECALL_TOP_K,
                    "teams": list(visible_team_ids),
                },
            )
        ).all()
        return [
            RecallHit(
                schema_name=r.schema_name,
                table_name=r.table_name,
                score=float(r.score),
                source="vector",
            )
            for r in rows
        ]

    async def has_knowledge(self, *, tenant_id: str, datasource_key: str) -> bool:
        """闸门前置:零知识数据源闸门自动旁路——零命中源于「无知识」
        而非「低相关」,此时裁剪召回会误杀内省回退路径的可答题。
        EXISTS 单点查,零成本。"""
        row = (
            await self._s.execute(
                text(
                    "SELECT EXISTS ("
                    " SELECT 1 FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d)"
                ),
                {"t": tenant_id, "d": datasource_key},
            )
        ).scalar()
        return bool(row)

    @property
    def _ft_and_pred(self) -> str:
        """AND 轮谓词:全词条匹配。"""
        return f" AND tsv @@ plainto_tsquery('{self._ts}', :q)"

    @property
    def _ft_or_pred(self) -> str:
        """内容词兜底轮谓词:任一**内容词**词条命中——词条限
        length >= 2,从谓词上排除单字虚词噪声源(『有』『部』
        类高频词条挤占 RRF 位次);词条逐个过 plainto_tsquery(对任意
        文本安全,内部转义),外部文本不进 to_tsquery 语法位。"""
        return (
            " AND EXISTS (SELECT 1 FROM"  # noqa: S608 # {self._ts} 为白名单校验后的配置名(标识符无法绑定参数)
            f" unnest(tsvector_to_array(to_tsvector('{self._ts}', :q))) AS tok"
            " WHERE length(tok) >= 2"
            f"   AND tsv @@ plainto_tsquery('{self._ts}', tok))"
        )

    async def _fulltext_rows(
        self,
        *,
        predicate: str,
        tenant_id: str,
        datasource_key: str,
        question: str,
        visible_team_ids: Sequence[str],
    ) -> list[RecallHit]:
        """共享 SELECT 主体(排序/量纲/过滤单点;仅匹配谓词随轮次变化)。"""
        rows = (
            await self._s.execute(
                text(
                    # S608 豁免:{self._ts} 为白名单校验后的配置名(标识符
                    # 无法绑定参数),谓词为类内常量形态,其余全部绑定参数
                    "SELECT schema_name, table_name,"  # noqa: S608
                    f" ts_rank(tsv, plainto_tsquery('{self._ts}', :q)) AS score"
                    " FROM rag.table_descriptions"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    f"{predicate}"
                    " AND (team_id IS NULL OR team_id::text = ANY(CAST(:teams AS text[])))"
                    f" ORDER BY ts_rank(tsv, plainto_tsquery('{self._ts}', :q)) DESC"
                    " LIMIT :k"
                ),
                {
                    "t": tenant_id,
                    "d": datasource_key,
                    "q": question,
                    "k": RECALL_TOP_K,
                    "teams": list(visible_team_ids),
                },
            )
        ).all()
        return [
            RecallHit(
                schema_name=r.schema_name,
                table_name=r.table_name,
                score=float(r.score),
                source="fulltext",
            )
            for r in rows
        ]

    async def recall_fulltext(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        visible_team_ids: Sequence[str] = (),
    ) -> list[RecallHit]:
        """全文检索先按全部词项做 AND 匹配，零命中时再按长度至少为 2 的内容词检索。
        返回与排序共用 ts_rank；写入和查询使用相同分词配置，并叠加团队可见性过滤。
        """
        for predicate in (self._ft_and_pred, self._ft_or_pred):
            hits = await self._fulltext_rows(
                predicate=predicate,
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=question,
                visible_team_ids=visible_team_ids,
            )
            if hits:
                return hits
        return []

    async def recall_history(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        vector: list[float],
        visible_team_ids: Sequence[str] = (),
    ) -> list[HistorySample]:
        """召回已启用的 EXECUTION 或 FEEDBACK SQL 样本；同问句优先保留 ADOPTED
        反馈样本。团队样本只对可见团队成员开放，无身份调用仅能读取公共样本。
        """
        rows = (
            await self._s.execute(
                text(
                    "SELECT question, sql_text, source_type,"
                    " 1 - (embedding <=> CAST(:vec AS halfvec)) AS score"
                    " FROM rag.query_history"
                    " WHERE tenant_id = :t AND datasource_key = :d"
                    " AND status = 'SUCCESS' AND recall_enabled"
                    " AND source_type IN ('EXECUTION', 'FEEDBACK')"
                    " AND embedding IS NOT NULL"
                    " AND (COALESCE(cardinality(visible_teams), 0) = 0"
                    "      OR visible_teams && CAST(:teams AS text[]))"
                    " ORDER BY embedding <=> CAST(:vec AS halfvec) LIMIT :k"
                ),
                {
                    "t": tenant_id,
                    "d": datasource_key,
                    "vec": vec_literal(vector),
                    "teams": list(visible_team_ids),
                    "k": RECALL_TOP_K * 3,
                },
            )
        ).all()
        return [
            HistorySample(
                question=str(r.question),
                sql_text=str(r.sql_text),
                source_type=str(r.source_type),
                score=float(r.score),
            )
            for r in rows
        ]

    async def fetch_entries(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        keys: list[tuple[str, str]],
        visible_team_ids: Sequence[str] = (),
    ) -> list[KnowledgeEntry]:
        """按 (schema, table) 集取条目内容(四维限定,防同名表跨 schema 串扰)。

        同款可见集合过滤——兜底关系扩展(semantic 层不识归属)引入的
        键,团队条目 MUST NOT 经扩展路进入注入。"""
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
                        " AND (team_id IS NULL OR team_id::text = ANY(CAST(:teams AS text[])))"
                    ),
                    {
                        "t": tenant_id,
                        "d": datasource_key,
                        "s": schema,
                        "n": table,
                        "teams": list(visible_team_ids),
                    },
                )
            ).all()
            out.extend(to_entry(r, tenant_id) for r in rows)
        return out


class RagSampleRepository:
    """few-shot 样本回写、反馈样本晋升/停用。"""

    def __init__(self, core: RagRepository) -> None:
        self._s = core.session
        self._ts = core.ts_config

    # ============ 反馈样本晋升/停用(与处置同事务,不自行 commit) ============

    async def promote_feedback_sample(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        feedback_message_id: str,
        embedding: list[float] | None,
        visible_teams: Sequence[str] = (),
    ) -> None:
        """纠错 SQL 晋升为可召回样本(FEEDBACK,recall_enabled=TRUE)。

        幂等键=(tenant_id, feedback_message_id) 部分唯一索引:同一反馈的
        重复晋升覆盖(最新纠错);与执行样本(同问句 EXECUTION)并存不互覆盖
        ——「同问句异来源并存,召回按优先级择优」。伴生:visible_teams
        =反馈发起者可见集合(空=公共)。"""
        vec = vec_literal(embedding) if embedding else None
        await self._s.execute(
            text(
                "INSERT INTO rag.query_history"
                " (id, tenant_id, datasource_key, question, sql_text, status,"
                "  source_type, recall_enabled, feedback_message_id, embedding,"
                "  visible_teams, created_at)"
                " VALUES (:id, :t, :d, :q, :sql, 'SUCCESS', 'FEEDBACK', TRUE,"
                "         CAST(:mid AS uuid), CAST(:vec AS halfvec),"
                "         CAST(:teams AS text[]), now())"
                " ON CONFLICT (tenant_id, feedback_message_id)"
                "   WHERE source_type = 'FEEDBACK'"
                "   AND feedback_message_id IS NOT NULL DO UPDATE SET"
                "  sql_text = EXCLUDED.sql_text,"
                "  embedding = COALESCE(EXCLUDED.embedding, query_history.embedding),"
                "  visible_teams = EXCLUDED.visible_teams,"
                "  status = 'SUCCESS', recall_enabled = TRUE, created_at = now()"
            ),
            {
                "id": str(uuid7()),
                "t": tenant_id,
                "d": datasource_key,
                "q": question,
                "sql": sql_text,
                "mid": feedback_message_id,
                "vec": vec,
                "teams": sorted(set(visible_teams)),
            },
        )

    async def disable_feedback_samples(self, *, feedback_message_id: str) -> None:
        """停用该反馈消息关联的样本(PENDING/REJECTED/DISMISSED/重复提交的
        旧样本:退出召回;行保留(来源审计),SHALL NOT 删)。"""
        await self._s.execute(
            text(
                "UPDATE rag.query_history SET recall_enabled = FALSE"
                " WHERE source_type = 'FEEDBACK'"
                " AND feedback_message_id = CAST(:mid AS uuid)"
            ),
            {"mid": feedback_message_id},
        )

    # ============ 回写 ============

    async def insert_history(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        embedding: list[float] | None,
        visible_teams: Sequence[str] = (),
    ) -> None:
        """幂等回写(治理):同 (tenant, ds, question) 覆盖保留最新成功 SQL
        (0007 唯一索引支撑);embedding 失败传 None 时保留旧向量(旧向量语义
        仍有效,清空会使该行退出向量召回);同事务上限淘汰该维度最旧行。
        伴生:visible_teams=回写时调用者可见集合(排序去重;空=公共),
        覆盖更新同步刷新(同题重写以最新一次来源为准)。
        """
        vec = vec_literal(embedding) if embedding else None
        teams = sorted(set(visible_teams))
        await self._s.execute(
            text(
                "INSERT INTO rag.query_history"
                " (id, tenant_id, datasource_key, question, sql_text, status,"
                "  source_type, recall_enabled, embedding, visible_teams, created_at)"
                " VALUES (:id, :t, :d, :q, :sql, 'SUCCESS', 'EXECUTION', TRUE,"
                "         CAST(:vec AS halfvec), CAST(:teams AS text[]), now())"
                # 执行样本幂等目标=部分唯一索引(谓词须逐字匹配)
                " ON CONFLICT (tenant_id, datasource_key, md5(question))"
                "   WHERE source_type = 'EXECUTION' DO UPDATE SET"
                "  sql_text = EXCLUDED.sql_text,"
                "  embedding = COALESCE(EXCLUDED.embedding, query_history.embedding),"
                "  visible_teams = EXCLUDED.visible_teams,"
                "  status = 'SUCCESS', recall_enabled = TRUE, created_at = now()"
            ),
            {
                "id": str(uuid7()),
                "t": tenant_id,
                "d": datasource_key,
                "q": question,
                "sql": sql_text,
                "vec": vec,
                "teams": teams,
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


class RagTenantRepository:
    """租户解析、数据源可用性复校与全量内省/可扫描 schema。"""

    def __init__(self, core: RagRepository) -> None:
        self._s = core.session
        self._ts = core.ts_config

    # ============ 租户解析(与 chat/options 白名单复校同构) ============

    async def resolve_bound_tenant(self, user_id: str, tenant_key: str) -> tuple[str, str] | None:
        repo = SqlDatasourceRepo(self._s)
        tenant_id = await repo.find_bound_tenant(user_id, tenant_key)
        return (tenant_id, tenant_key) if tenant_id is not None else None

    async def resolve_default_tenant(self, user_id: str) -> tuple[str, str] | None:
        return await SqlDatasourceRepo(self._s).find_default_tenant(user_id)

    async def tenant_id_by_key(self, tenant_key: str) -> str | None:
        """内部召回端点租户解析(与 meta 通道同语义:未知/禁用同形 404)。

        统一转 str:is_usable 的归属比对在 str 域(asyncpg 返回 UUID 对象
        会使合法数据源被恒拒)。
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

    # ============ 全量内省(与 meta 通道同口径,无截断) ============

    async def introspect_all(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[IntrospectedTable]:
        """default 数据源只读取租户 schema 与 public；动态数据源由 resolver 校验，
        并通过受限连接池内省。
        """
        if datasource_key == "default":
            return await introspect_all_on(self._s, allowed_schemas_for(tenant_key, None))
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
            return await introspect_all_on(
                conn, allowed_schemas_for(tenant_key, row.default_schema)
            )

    async def allowed_schemas(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[str]:
        """可扫描 schema 集合(枚举端点/上传存在性闸,与内省同源同口径)。

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
        """知识条目表存在性(semantic 域手工关系闸经端口消费)。

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
