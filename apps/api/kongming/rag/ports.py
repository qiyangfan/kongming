"""rag 域端口:以 typing.Protocol 隔离用例层与存储实现。

service 零框架:持久化/内省/租户解析/数据源校验全部经端口;
repo 实现见 repository.py(SQLAlchemy + 动态池内省编排)。
"""

from collections.abc import Coroutine, Sequence
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
    """成功 SQL 回写窄端口(chat 域消费;实现=RagService.writeback)。

    伴生:caller_user_id 决定样本可见集合(公共∪其团队)——团队条目
    引出的 SQL 不经其成员授权进入他人 few-shot;缺省(None)=公共样本。"""

    async def writeback(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        caller_user_id: str | None = None,
    ) -> None: ...


class ColumnCommentReader(Protocol):
    """列注释读取窄端口(呈现层列标签;chat 域消费;实现=RagService)。

    返回 {表名: {列名: 注释原文(含 PG 类型前缀,剥离在呈现纯函数内)}}——
    单数据源注释集量级(数十表×数十列),整集返回免 SQL 解析与按表查询。"""

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]: ...


class SuggestionCacheEvictor(Protocol):
    """知识条目变化时清除相关建议缓存；删除操作与知识写事务一并提交。"""

    def delete_by_datasource(self, datasource_key: str) -> Coroutine[Any, Any, None]: ...


class RelationExpansionPort(Protocol):
    """按已声明的表关系扩展召回结果，由组合根注入实现。"""

    async def related_tables(
        self, *, tenant_id: str, datasource_key: str, tables: list[tuple[str, str]]
    ) -> list[dict[str, Any]]: ...


class MemberTeamsReader(Protocol):
    """调用者团队集合只读窄端口(可见集合;实现=team 域 SqlTeamRepo
    组合适配,组合根注入,team 域零改动)。

    返回该用户全部团队 id(角色不分——可见性按「所在团队」判,VIEWER 亦在
    集合内,与 team/models.visible 同判)。未注入/无身份=空集合=纯公共形态
    (既有调用方)。"""

    async def team_ids_of_user(self, user_id: str) -> list[str]: ...


class MemberRoleReader(Protocol):
    """成员角色只读窄端口(写权限;实现=team 域 SqlTeamRepo 组合适配)。

    返回调用者在指定团队的角色(None=非成员),供 entry_write_access 三态
    判定(OWNER/MEMBER 可写、VIEWER 403、非成员 404 同形)。tenant_id 参与判定:
    角色查询与团队归属租户绑定(跨租户活跃用户不得借他租户团队身份过闸)。"""

    async def role_of(self, tenant_id: str, team_id: str, user_id: str) -> str | None: ...


class TableDescriberPort(Protocol):
    """表元数据润色(api→agent /internal/describe-table;enriched=False 即降级纯结构)。"""

    async def describe(self, block: TableBlock, schema_name: str) -> tuple[bool, TableBlock]: ...


class RagEntryRepo(Protocol):
    """知识库持久化端口的表条目读写、团队归属与数据源内省面(仅供类型组合,运行期按结构判定)。"""

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
        alias: str = "",
        team_id: str | None = None,  # 归属值(set_team_id=True 时生效)
        set_team_id: bool = False,  # False=保留既有归属(缺省)
    ) -> None: ...

    async def set_entry_team(self, *, tenant_id: str, table_id: str, team_id: str | None) -> bool:
        """归属变更原位置 NULL⇄team_id(单一实例,内容不动)。"""
        ...

    async def publicize_team_entries(self, *, team_id: str) -> list[tuple[str, str]]:
        """团队删除钩子:名下条目归属置空,返回受影响 (tenant, ds) 范围。"""
        ...

    async def list_tables(
        self,
        *,
        tenant_id: str,
        datasource_key: str | None,
        limit: int,
        offset: int,
        team_scope: str = "all",  # all|public|<team_id>(可见集合内筛选)
        visible_team_ids: Sequence[str] = (),  # 调用者团队集合
    ) -> tuple[list[KnowledgeEntry], int]: ...

    async def find_table(self, *, tenant_id: str, table_id: str) -> KnowledgeEntry | None: ...

    async def delete_table(self, *, tenant_id: str, table_id: str) -> bool: ...

    async def delete_all_for_datasource(self, *, tenant_id: str, datasource_key: str) -> int: ...

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]: ...

    async def list_source_and_alias(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[tuple[str, str], tuple[str, str, str | None]]:
        """(schema_name, table_name) → (source_type, alias, team_id)——二元键防
        跨 schema 同名表互遮;team_id 供上传写闸判既有条目归属。"""
        ...

    async def delete_stale_entries(
        self, *, tenant_id: str, datasource_key: str, keep: list[tuple[str, str]]
    ) -> tuple[int, int]:
        """删除当前扫描结果之外的自动来源条目，返回删除数和保留的人工孤儿数。"""
        ...


class RagRecallRepo(Protocol):
    """知识库持久化端口的三路召回与知识存在闸面(仅供类型组合,运行期按结构判定)。"""

    async def recall_vector(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        vector: list[float],
        visible_team_ids: Sequence[str] = (),  # 可见集合过滤
    ) -> list[RecallHit]: ...

    async def has_knowledge(self, *, tenant_id: str, datasource_key: str) -> bool:
        """该数据源是否已灌表知识(闸门前置:零知识≠低相关,见 gate 消费点)。"""
        ...

    async def recall_fulltext(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        visible_team_ids: Sequence[str] = (),  # 可见集合过滤
    ) -> list[RecallHit]: ...

    async def recall_history(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        vector: list[float],
        visible_team_ids: Sequence[str] = (),  # 伴生:样本可见集合过滤
    ) -> list[HistorySample]: ...

    async def fetch_entries(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        keys: list[tuple[str, str]],
        visible_team_ids: Sequence[str] = (),  # 可见集合过滤(扩展路兜底)
    ) -> list[KnowledgeEntry]: ...


class RagSampleRepo(Protocol):
    """知识库持久化端口的历史样本写入与反馈样本晋升/停用面(仅供类型组合,运行期按结构判定)。"""

    async def insert_history(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        embedding: list[float] | None,
        visible_teams: Sequence[str] = (),  # 伴生:样本来源可见集合(空=公共)
    ) -> None: ...

    # 反馈样本晋升/停用(与反馈处置同事务;调用方负责 commit) ----

    async def promote_feedback_sample(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        feedback_message_id: str,
        embedding: list[float] | None,
        visible_teams: Sequence[str] = (),  # 伴生:样本来源可见集合(空=公共)
    ) -> None: ...

    async def disable_feedback_samples(self, *, feedback_message_id: str) -> None: ...


class RagCoreRepo(Protocol):
    """知识库持久化端口的事务边界与审计写入面(仅供类型组合,运行期按结构判定)。"""

    async def log_oper(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    # ---- 租户解析(与 chat/options 白名单复校同构) ----


class RagTenantRepo(Protocol):
    """知识库持久化端口的租户解析、数据源可用性与内省面(仅供类型组合,运行期按结构判定)。"""

    async def resolve_bound_tenant(
        self, user_id: str, tenant_key: str
    ) -> tuple[str, str] | None: ...

    async def resolve_default_tenant(self, user_id: str) -> tuple[str, str] | None: ...

    async def tenant_id_by_key(self, tenant_key: str) -> str | None: ...

    async def datasource_usable(self, datasource_key: str, tenant_id: str) -> bool: ...

    async def allowed_schemas(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[str]:
        """可扫描 schema 集合(上传存在性校验与枚举端点候选源,与内省同源)。"""
        ...

    async def introspect_all(
        self, *, tenant_id: str, tenant_key: str, datasource_key: str
    ) -> list[IntrospectedTable]: ...

    async def knowledge_table_exists(
        self, *, tenant_id: str, datasource_key: str, schema_table: str
    ) -> bool: ...


class RagRepo(RagCoreRepo, Protocol):
    """知识库持久化的整体视图:事务收口 + 各能力子仓储(同一会话)。

    服务按用例只依赖所用子仓储窄面;聚合视图供装配与跨用例测试注解使用。
    """

    entries: RagEntryRepo
    recall: RagRecallRepo
    samples: RagSampleRepo
    tenant: RagTenantRepo
    coverage: "CoverageRepo"


class CoverageRepo(Protocol):
    """知识覆盖报告的只读聚合输入端口(零写入零 LLM;实现见 coverage_repository)。"""

    async def list_all_entries(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        visible_team_ids: Sequence[str] = (),
    ) -> list[KnowledgeEntry]:
        """该 (租户, 数据源) 登记条目(无分页;治理面整集盘点)。

        可见集合=公共 ∪ 调用者团队(覆盖报告读面与列表同口径)。"""
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
