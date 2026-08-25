"""rag 域端口(009):领域依赖反转(宪法 Article II,typing.Protocol)。

service 零框架:持久化/内省/租户解析/数据源校验全部经端口;
repo 实现见 repository.py(SQLAlchemy + 动态池内省编排)。
"""

from typing import Protocol

from kongming.rag.models import IntrospectedTable, KnowledgeEntry, RecallHit, TableBlock


class EmbeddingError(Exception):
    """embedding 服务不可用(端点失败/网络/超时)——调用方按降级处理。"""


class EmbeddingPort(Protocol):
    """批量向量化(api→agent /internal/embedding;LLM key 仅 agent 面持有,ADR-002)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class QueryHistoryWriter(Protocol):
    """成功 SQL 回写窄端口(chat 域消费;实现=RagService.writeback)。"""

    async def writeback(
        self, *, tenant_id: str, datasource_key: str, question: str, sql_text: str
    ) -> None: ...


class TableDescriberPort(Protocol):
    """表元数据润色(api→agent /internal/describe-table;enriched=False 即降级纯结构)。"""

    async def describe(
        self, block: TableBlock, schema_name: str
    ) -> tuple[bool, TableBlock]: ...


class RagRepo(Protocol):
    """rag 持久化与基础设施端口(实现组合审计仓储,同会话=同事务,007 惯例)。"""

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
        alias: str = "",  # 037
    ) -> None: ...

    async def list_tables(
        self,
        *,
        tenant_id: str,
        datasource_key: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[KnowledgeEntry], int]: ...

    async def find_table(self, *, tenant_id: str, table_id: str) -> KnowledgeEntry | None: ...

    async def delete_table(self, *, tenant_id: str, table_id: str) -> bool: ...

    async def delete_all_for_datasource(self, *, tenant_id: str, datasource_key: str) -> int: ...

    async def list_source_and_alias(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, tuple[str, str]]: ...

    async def recall_vector(
        self, *, tenant_id: str, datasource_key: str, vector: list[float]
    ) -> list[RecallHit]: ...

    async def recall_fulltext(
        self, *, tenant_id: str, datasource_key: str, question: str
    ) -> list[RecallHit]: ...

    async def recall_history(
        self, *, tenant_id: str, datasource_key: str, vector: list[float]
    ) -> list[str]: ...

    async def fetch_entries(
        self, *, tenant_id: str, datasource_key: str, keys: list[tuple[str, str]]
    ) -> list[KnowledgeEntry]: ...

    async def insert_history(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        embedding: list[float] | None,
    ) -> None: ...

    async def log_oper(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    # ---- 租户解析(与 chat/options 白名单复校同构) ----

    async def resolve_bound_tenant(
        self, user_id: str, tenant_key: str
    ) -> tuple[str, str] | None: ...

    async def resolve_default_tenant(self, user_id: str) -> tuple[str, str] | None: ...

    async def tenant_id_by_key(self, tenant_key: str) -> str | None: ...

    async def datasource_usable(self, datasource_key: str, tenant_id: str) -> bool: ...

    async def introspect_all(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[IntrospectedTable]: ...
