"""外键内省共享查询:default 会话与动态数据源只读连接同一条 SQL。"""

from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


async def fetch_fk_pairs(
    conn: AsyncSession | AsyncConnection, *, schemas: list[str]
) -> list[dict[str, Any]]:
    """按 schema 集取外键列对(多列外键按 position_in_unique_constraint 配对)。"""
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT fk.table_schema, fk.table_name, fk.column_name,"
                    "       pk.table_schema AS f_schema, pk.table_name AS f_table,"
                    "       pk.column_name AS f_col, fk.constraint_name,"
                    "       fk.ordinal_position"
                    " FROM information_schema.table_constraints tc"
                    " JOIN information_schema.key_column_usage fk"
                    "   ON tc.constraint_name=fk.constraint_name"
                    "  AND tc.table_schema=fk.table_schema"
                    " JOIN information_schema.referential_constraints rc"
                    "   ON tc.constraint_name=rc.constraint_name"
                    "  AND tc.constraint_schema=rc.constraint_schema"
                    " JOIN information_schema.key_column_usage pk"
                    "   ON rc.unique_constraint_name=pk.constraint_name"
                    "  AND rc.unique_constraint_schema=pk.constraint_schema"
                    "  AND pk.ordinal_position = fk.position_in_unique_constraint"
                    " WHERE tc.constraint_type='FOREIGN KEY'"
                    "   AND fk.table_schema = ANY(CAST(:ss AS text[]))"
                ),
                {"ss": schemas},
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "source_table": f"{r['table_schema']}.{r['table_name']}",
            "target_table": f"{r['f_schema']}.{r['f_table']}",
            "left_col": str(r["column_name"]),
            "right_col": str(r["f_col"]),
            "constraint_name": str(r["constraint_name"]),
            "ordinal": int(cast("int", r["ordinal_position"])),
        }
        for r in rows
    ]


class ConnectionFkIntrospector:
    """FkIntrospector 连接绑定实现(默认会话/动态只读连接共用)。"""

    def __init__(
        self,
        conn: AsyncSession | AsyncConnection,
        *,
        schemas: list[str],
    ) -> None:
        self._conn = conn
        self._schemas = schemas

    async def fetch_fk_pairs(self, *, schemas: list[str]) -> list[dict[str, Any]]:
        # 端口参数保留语义,但绑定 schema 集优先:调用方不得越出数据域
        del schemas
        return await fetch_fk_pairs(self._conn, schemas=self._schemas)
