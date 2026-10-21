"""semantic 仓储:table_relations/metric_definitions SQL。"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7


class SqlSemanticRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def commit(self) -> None:
        await self._s.commit()

    # ── 表关系 ──

    async def upsert_introspection(
        self, *, tenant_id: str, datasource_key: str, rels: list[dict[str, Any]]
    ) -> int:
        """introspection 幂等 upsert;manual 行不被覆盖(WHERE 守卫)。"""
        for r in rels:
            await self._s.execute(
                text(
                    "INSERT INTO rag.table_relations"
                    " (id, tenant_id, datasource_key, source_table, target_table,"
                    "  join_keys, source)"
                    " VALUES (:i, :t, :d, :st, :tt, CAST(:jk AS jsonb), 'introspection')"
                    " ON CONFLICT (tenant_id, datasource_key, source_table, target_table)"
                    " DO UPDATE SET join_keys = CAST(:jk AS jsonb), updated_at = now()"
                    " WHERE rag.table_relations.source = 'introspection'"
                ),
                {
                    "i": str(uuid7()),
                    "t": tenant_id,
                    "d": datasource_key,
                    "st": r["source_table"],
                    "tt": r["target_table"],
                    "jk": json.dumps(r["join_keys"]),
                },
            )
        return len(rels)

    async def list_relations(self, *, tenant_id: str, datasource_key: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, source_table, target_table, join_keys, source, updated_at"
                        " FROM rag.table_relations"
                        " WHERE tenant_id=:t AND datasource_key=:d"
                        " ORDER BY source_table, target_table"
                    ),
                    {"t": tenant_id, "d": datasource_key},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def relations_within(
        self, *, tenant_id: str, datasource_key: str, tables: list[str]
    ) -> list[dict[str, Any]]:
        """仅取表集合内的两两关系(注入收窄,spec 立约)。
        单表集合也查:ANY(ts) 天然收窄,可命中自引用 FK 行(goods→goods)。"""
        if not tables:
            return []
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT source_table, target_table, join_keys, source"
                        " FROM rag.table_relations"
                        " WHERE tenant_id=:t AND datasource_key=:d"
                        "   AND source_table = ANY(:ts) AND target_table = ANY(:ts)"
                    ),
                    {"t": tenant_id, "d": datasource_key, "ts": tables},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def relations_touching(
        self, *, tenant_id: str, datasource_key: str, tables: list[str]
    ) -> list[dict[str, Any]]:
        """一跳扩展查询:任一端在表集合内即命中(与 relations_within 的
        差别只在谓词:within=两端均在(注入收窄),touching=任一端在)。
        ORDER BY 保证输出序确定。"""
        if not tables:
            return []
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT source_table, target_table, join_keys, source"
                        " FROM rag.table_relations"
                        " WHERE tenant_id=:t AND datasource_key=:d"
                        "   AND (source_table = ANY(:ts) OR target_table = ANY(:ts))"
                        " ORDER BY source_table, target_table"
                    ),
                    {"t": tenant_id, "d": datasource_key, "ts": tables},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def add_relation(
        self, *, tenant_id: str, datasource_key: str, rel: dict[str, Any]
    ) -> str:
        row_id = (
            await self._s.execute(
                text(
                    "INSERT INTO rag.table_relations"
                    " (id, tenant_id, datasource_key, source_table, target_table,"
                    "  join_keys, source)"
                    " VALUES (:i, :t, :d, :st, :tt, CAST(:jk AS jsonb), 'manual')"
                    " ON CONFLICT (tenant_id, datasource_key, source_table, target_table)"
                    # 手工接管——冲突翻转 source=manual(防下次扫描被还原);
                    # RETURNING 真实行 id(此前返回新 uuid 指向不存在行)
                    " DO UPDATE SET join_keys = CAST(:jk AS jsonb), source='manual',"
                    "   updated_at = now()"
                    " RETURNING id"
                ),
                {
                    "i": str(uuid7()),
                    "t": tenant_id,
                    "d": datasource_key,
                    "st": rel["source_table"],
                    "tt": rel["target_table"],
                    "jk": json.dumps(rel["join_keys"]),
                },
            )
        ).scalar_one()
        return str(row_id)

    async def delete_relation(self, *, relation_id: str, tenant_id: str) -> str | None:
        """删除命中返回该行的 datasource_key(提交后按其精确失效检索
        缓存),不命中返回 None(同形,防探测)。"""
        ds = (
            await self._s.execute(
                text(
                    "DELETE FROM rag.table_relations WHERE id=:i AND tenant_id=:t"
                    " RETURNING datasource_key"
                ),
                {"i": relation_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        return None if ds is None else str(ds)

    async def delete_by_datasource(self, *, tenant_id: str, datasource_key: str) -> None:
        """数据源删除级联(spec 立约);metrics 同清。"""
        await self._s.execute(
            text("DELETE FROM rag.table_relations WHERE tenant_id=:t AND datasource_key=:d"),
            {"t": tenant_id, "d": datasource_key},
        )
        await self._s.execute(
            text("DELETE FROM rag.metric_definitions WHERE tenant_id=:t AND datasource_key=:d"),
            {"t": tenant_id, "d": datasource_key},
        )

    # ── 指标 ──

    async def list_metrics(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        visible_dept_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        # 可见性过滤在 SQL 内单点判定:
        # visible_dept_ids=None=不过滤(管理面全量视角/ALL 档调用者);
        # 传序列(含空数组)=ALL 档 ∪「DEPT 档且 dept_id 命中集合」,空数组
        # 退化为仅 ALL 档(SELF/无身份/CUSTOM 空集的读面降级形态)。
        vis = ""
        params: dict[str, Any] = {"t": tenant_id, "d": datasource_key}
        if visible_dept_ids is not None:
            vis = (
                " AND (visible_scope='ALL'"
                " OR (visible_scope='DEPT' AND dept_id = ANY(CAST(:depts AS uuid[]))))"
            )
            params["depts"] = visible_dept_ids
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, metric_name, sql_template, business_terms, enabled,"  # noqa: S608  # 常量模板,参数化绑定
                        " updated_at, visible_scope, dept_id"
                        " FROM rag.metric_definitions"
                        f" WHERE tenant_id=:t AND datasource_key=:d{vis}"  # noqa: S608  # 常量模板,参数化绑定
                        " ORDER BY metric_name"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def upsert_metric(
        self, *, tenant_id: str, datasource_key: str, metric: dict[str, Any]
    ) -> str:
        visible_scope = str(metric.get("visible_scope") or "ALL")
        dept_id = metric.get("dept_id")
        row_id = (
            await self._s.execute(
                text(
                    "INSERT INTO rag.metric_definitions"
                    " (id, tenant_id, datasource_key, metric_name, sql_template,"
                    "  business_terms, enabled, visible_scope, dept_id)"
                    " VALUES (:i, :t, :d, :n, :tpl, :terms, :en, :vs, :dp)"
                    " ON CONFLICT (tenant_id, datasource_key, metric_name)"
                    " DO UPDATE SET sql_template=:tpl, business_terms=:terms,"
                    "   enabled=:en, visible_scope=:vs, dept_id=:dp, updated_at=now()"
                    " RETURNING id"  # 真实行 id
                ),
                {
                    "i": str(uuid7()),
                    "t": tenant_id,
                    "d": datasource_key,
                    "n": metric["metric_name"],
                    "tpl": metric["sql_template"],
                    "terms": metric.get("business_terms") or [],
                    "en": metric.get("enabled", True),
                    "vs": visible_scope,
                    "dp": dept_id,
                },
            )
        ).scalar_one()
        return str(row_id)

    async def delete_metric(self, *, metric_id: str, tenant_id: str) -> bool:
        hit = (
            await self._s.execute(
                text(
                    "DELETE FROM rag.metric_definitions WHERE id=:i AND tenant_id=:t RETURNING id"
                ),
                {"i": metric_id, "t": tenant_id},
            )
        ).scalar_one_or_none()
        return hit is not None
