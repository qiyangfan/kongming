"""semantic 端口(022→023):service 依赖反转。零框架纪律:仅 Protocol。"""

from typing import Any, Protocol


class FkIntrospector(Protocol):
    """外键扫描(基础设施适配:default=控制面会话/动态源=只读连接)。"""

    async def fetch_fk_pairs(
        self, *, schemas: list[str]
    ) -> list[dict[str, Any]]:  # [{source_table, target_table, left_col, right_col}]
        ...


class SemanticRepo(Protocol):
    """table_relations/metric_definitions 存取(仓储实现)。"""

    async def commit(self) -> None: ...

    async def upsert_introspection(
        self, *, tenant_id: str, datasource_key: str, rels: list[dict[str, Any]]
    ) -> int: ...

    async def relations_within(
        self, *, tenant_id: str, datasource_key: str, tables: list[str]
    ) -> list[dict[str, Any]]: ...

    async def list_metrics(
        self, *, tenant_id: str, datasource_key: str
    ) -> list[dict[str, Any]]: ...

    async def add_relation(
        self, *, tenant_id: str, datasource_key: str, rel: dict[str, Any]
    ) -> str: ...

    async def delete_relation(self, *, relation_id: str, tenant_id: str) -> str | None:
        """删除命中返回该行的 datasource_key(095:提交后按其精确失效缓存),
        不命中返回 None(同形 404,防探测)。"""


class KnowledgeTableLookup(Protocol):
    """知识条目表存在性(040:手工关系写边界闸;实现=rag 知识仓储查询)。

    schema_table 为「schema.table」形态;引用知识条目外的表在召回
    relations_hint 中永不命中(静默死配置),故创建期即拒。"""

    async def knowledge_table_exists(
        self, *, tenant_id: str, datasource_key: str, schema_table: str
    ) -> bool: ...


class RecallContextEvictor(Protocol):
    """检索上下文缓存失效窄端口(095:关系写路径消费;实现=rag ContextCache,
    组合根注入——semantic 域只见 Protocol,不 import rag)。

    关系写(introspection upsert / 手工增删 / 数据源删除级联)提交后必须
    失效该租户+数据源的全部检索上下文缓存:召回输出自此依赖关系数据,
    不失效则改动在缓存 TTL 窗口内不可见(「配了却没变化」的静默失效)。"""

    async def evict(self, tenant_id: str, datasource_key: str) -> None: ...
