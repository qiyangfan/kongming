"""rag 域端口(009):领域依赖反转(宪法 Article II,typing.Protocol)。

service 零框架:持久化/内省/租户解析/数据源校验全部经端口;
repo 实现见 repository.py(SQLAlchemy + 动态池内省编排)。
"""

from collections.abc import Coroutine
from datetime import datetime
from typing import Any, Protocol

from kongming.rag.models import (
    FailureSignal,
    HistoryOverview,
    HistorySample,
    IntrospectedTable,
    KnowledgeEntry,
    RecallHit,
    TableBlock,
)


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


class ColumnCommentReader(Protocol):
    """列注释读取窄端口(041 呈现层列标签;chat 域消费;实现=RagService)。

    返回 {表名: {列名: 注释原文(含 PG 类型前缀,剥离在呈现纯函数内)}}——
    单数据源注释集量级(数十表×数十列),整集返回免 SQL 解析与按表查询。"""

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]: ...


class SuggestionCacheEvictor(Protocol):
    """建议问题缓存级联失效窄端口(047:知识写路径消费;实现=chat 域
    SuggestionRepository,结构化匹配;datasource 域同名端口的实现亦兼容)。

    DELETE 语句在知识写路径的业务事务内执行,由 rag commit 一并收口
    (039 datasource 级联先例同范式)——表结构知识变更(收录收窄/对账
    清偿/人工治理)若不失效建议缓存,存量建议会持续推荐已被治理排除的
    问题。实现侧经 _store_error_boundary 装饰,协议成员以 def+Coroutine
    形态声明(装饰器产物与 pyright 的协议匹配既定解法)。"""

    def delete_by_datasource(self, datasource_key: str) -> Coroutine[Any, Any, None]: ...


class TableDescriberPort(Protocol):
    """表元数据润色(api→agent /internal/describe-table;enriched=False 即降级纯结构)。"""

    async def describe(self, block: TableBlock, schema_name: str) -> tuple[bool, TableBlock]: ...


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

    async def allowed_schemas(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[str]:
        """可扫描 schema 集合(040:上传存在性校验与枚举端点候选源,与内省同源)。"""
        ...

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]: ...

    async def list_source_and_alias(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[tuple[str, str], tuple[str, str]]:
        """(schema_name, table_name) → (source_type, alias)——二元键防跨 schema
        同名表互遮(042)。"""
        ...

    async def delete_stale_entries(
        self, *, tenant_id: str, datasource_key: str, keep: list[tuple[str, str]]
    ) -> tuple[int, int]:
        """042 对账:删除扫描集外的自动来源登记行,返回 (removed, manual_orphans)。

        keep=本次扫描集 [(schema_name, table_name)];扫描集外人工/反馈行
        只计数不删(不可逆资产交人工裁决)。"""
        ...

    async def recall_vector(
        self, *, tenant_id: str, datasource_key: str, vector: list[float]
    ) -> list[RecallHit]: ...

    async def recall_fulltext(
        self, *, tenant_id: str, datasource_key: str, question: str
    ) -> list[RecallHit]: ...

    async def recall_history(
        self, *, tenant_id: str, datasource_key: str, vector: list[float]
    ) -> list[HistorySample]: ...

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

    # ---- 070 反馈样本晋升/停用(与反馈处置同事务;调用方负责 commit) ----

    async def promote_feedback_sample(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        feedback_message_id: str,
        embedding: list[float] | None,
    ) -> None: ...

    async def disable_feedback_samples(self, *, feedback_message_id: str) -> None: ...

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

    # ---- 052 知识覆盖报告(只读聚合输入;零写入零 LLM) ----

    async def list_all_entries(
        self, *, tenant_id: str, datasource_key: str
    ) -> list[KnowledgeEntry]:
        """该 (租户, 数据源) 全量登记条目(无分页;治理面整集盘点)。"""
        ...

    async def failure_signals(
        self, *, tenant_id: str, datasource_key: str, since: datetime
    ) -> list[FailureSignal]:
        """失败信号投影(chat_message 只读跨域,feedback 先例同范式;
        按 created_at 降序,行数受应用层上限约束)。"""
        ...

    async def history_overview(self, *, tenant_id: str, datasource_key: str) -> HistoryOverview:
        """few-shot 通道概况:成功沉淀条数/去重问题数/上限占用。"""
        ...
