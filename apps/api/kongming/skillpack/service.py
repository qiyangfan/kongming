"""skillpack 用例(056):CRUD/启停/投影加载;metrics 冲突拒绝启用。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from kongming.skillpack import models as sp
from kongming.skillpack.repository import SqlSkillPackRepo


class SkillPackNotFound(Exception):
    pass


class MetricConflictError(Exception):
    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(f"metric_name 冲突: {', '.join(names)}")


class SkillPackService:
    def __init__(
        self,
        repo: SqlSkillPackRepo,
        *,
        list_metric_names: Any = None,
        upsert_metric: Any = None,
        oper: Any = None,
    ) -> None:
        self._repo = repo
        self._list_metric_names = list_metric_names
        self._upsert_metric = upsert_metric
        self._oper = oper

    async def create(
        self,
        *,
        user_id: str,
        tenant_id: str,
        pack_key: str,
        name: str,
        version: str,
        description: str,
        content_raw: dict[str, Any],
    ) -> str:
        err = sp.validate_manifest(pack_key, name, version)
        if err:
            raise ValueError(err)
        content = sp.parse_content(content_raw)
        payload = {
            "glossary": [g.__dict__ for g in content.glossary],
            "fewshots": [f.__dict__ for f in content.fewshots],
            "persona": content.persona,
            "metrics": [m.__dict__ for m in content.metrics],
        }
        try:
            pid = await self._repo.insert_pack(
                tenant_id=tenant_id,
                pack_key=pack_key,
                name=name,
                version=version,
                description=description,
                content=payload,
            )
            await self._repo.commit()
        except IntegrityError as e:
            # (tenant_id, pack_key, version) 唯一;重复创建转域校验错误(422)
            raise ValueError("pack_key+version 已存在") from e
        if self._oper is not None:
            await self._oper.add(
                user_id=user_id,
                action="skillpack.create",
                target=pid,
                detail={"pack_key": pack_key},
            )
            await self._repo.commit()
        return pid

    async def get(self, *, pack_id: str, tenant_id: str) -> dict[str, Any]:
        row = await self._repo.get_pack(pack_id=pack_id, tenant_id=tenant_id)
        if row is None:
            raise SkillPackNotFound(pack_id)
        row["bindings"] = await self._repo.list_bindings(pack_id=pack_id)
        return row

    async def list(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return await self._repo.list_packs(tenant_id=tenant_id)

    async def retire(self, *, user_id: str, pack_id: str, tenant_id: str) -> None:
        ok = await self._repo.update_pack(pack_id=pack_id, tenant_id=tenant_id, status="RETIRED")
        if not ok:
            raise SkillPackNotFound(pack_id)
        await self._repo.commit()
        if self._oper is not None:
            await self._oper.add(
                user_id=user_id, action="skillpack.retire", target=pack_id, detail={}
            )
            await self._repo.commit()

    async def set_enabled(
        self,
        *,
        user_id: str,
        pack_id: str,
        tenant_id: str,
        datasource_key: str,
        enabled: bool,
    ) -> None:
        """启停 (pack, datasource)。启用含 metrics 时先做冲突检测,冲突则整体失败。"""
        row = await self._repo.get_pack(pack_id=pack_id, tenant_id=tenant_id)
        if row is None:
            raise SkillPackNotFound(pack_id)
        content = sp.parse_content(row.get("content") or {})
        if enabled and content.metrics:
            if self._list_metric_names is None or self._upsert_metric is None:
                raise MetricConflictError([m.metric_name for m in content.metrics])
            existing = await self._list_metric_names(
                tenant_id=tenant_id, datasource_key=datasource_key
            )
            # existing 可为名称列表或完整行;同名同定义放行(重启用幂等)
            conflicts = sp.metric_name_conflicts(content.metrics, existing)
            if conflicts:
                raise MetricConflictError(conflicts)
            for metric in content.metrics:
                await self._upsert_metric(
                    tenant_id=tenant_id,
                    datasource_key=datasource_key,
                    metric={
                        "metric_name": metric.metric_name,
                        "sql_template": metric.sql_template,
                        "business_terms": metric.business_terms,
                        "enabled": True,
                    },
                )
        await self._repo.set_binding(
            pack_id=pack_id, datasource_key=datasource_key, enabled=enabled
        )
        await self._repo.commit()
        if self._oper is not None:
            await self._oper.add(
                user_id=user_id,
                action="skillpack.bind",
                target=pack_id,
                detail={"datasource_key": datasource_key, "enabled": enabled},
            )
            await self._repo.commit()

    async def projection(self, *, tenant_id: str, datasource_key: str) -> sp.SkillProjection:
        """(租户, 数据源) enabled 包合并投影;零包=空段。"""
        rows = await self._repo.load_enabled(tenant_id=tenant_id, datasource_key=datasource_key)
        packs = [sp.parse_content(r.get("content") or {}) for r in rows]
        return sp.build_projection(packs)
