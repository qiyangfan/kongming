"""知识条目查询行 → 领域对象投影(多仓储共用)。"""

from kongming.rag.models import KnowledgeEntry


def to_entry(row: object, tenant_id: str) -> KnowledgeEntry:
    """查询行 → 领域条目(两仓储共用投影;缺归属列的行按公共形态)。"""
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
        alias=str(row.alias or ""),  # type: ignore[attr-defined] #
        # 归属字段(无该投影的调用面 Row 无属性→getattr 缺省公共形态)
        team_id=getattr(row, "team_id", None),
        team_name=getattr(row, "team_name", None),
    )
