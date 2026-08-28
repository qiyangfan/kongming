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
