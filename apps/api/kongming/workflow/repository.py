"""workflow 存储(029):sys_workflow_def/sys_workflow_run;版本冻结=
只插不改(无 UPDATE definition 路径);JSONB 经 CAST 绑定。"""

import json
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.uuid7 import uuid7
from kongming.workflow.models import DefinitionConflictError


class SqlWorkflowRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def next_version(self, *, tenant_id: str, wf_key: str) -> int:
        v = (
            await self._s.execute(
                text(
                    "SELECT max(version) FROM public.sys_workflow_def"
                    " WHERE tenant_id=CAST(:t AS uuid) AND wf_key=:k"
                ),
                {"t": tenant_id, "k": wf_key},
            )
        ).scalar_one()
        return int(v or 0) + 1

    async def insert_def(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        name: str,
        version: int,
        definition: dict[str, Any],
        created_by: str,
        team_id: Any = None,  # 032:继承上一版归属(R1-S2b)
    ) -> str:
        """插入新版本;唯一索引冲突(并发同版本)转域异常 409 语义。"""
        rid = str(uuid7())
        # R1-S2:版本冻结语义(design D1)——新版本产生即旧版本全失活;
        # 同事务,启用闸恒只作用最新版本(显式旧版本触发亦被拦)
        await self._s.execute(
            text(
                "UPDATE public.sys_workflow_def SET enabled=FALSE"
                " WHERE tenant_id=CAST(:t AS uuid) AND wf_key=:k"
            ),
            {"t": tenant_id, "k": wf_key},
        )
        try:
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_workflow_def"
                    " (id, tenant_id, wf_key, name, version, definition, created_by, team_id)"
                    " VALUES (:i, CAST(:t AS uuid), :k, :n, :v,"
                    "         CAST(:d AS jsonb), CAST(:u AS uuid), CAST(:tm AS uuid))"
                ),
                {
                    "i": rid,
                    "t": tenant_id,
                    "k": wf_key,
                    "n": name,
                    "v": version,
                    "d": json.dumps(definition, ensure_ascii=False),
                    "u": created_by,
                    "tm": None if team_id is None else str(team_id),
                },
            )
        except IntegrityError as e:
            raise DefinitionConflictError(wf_key) from e
        return rid

    async def latest_def(self, *, tenant_id: str, wf_key: str) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_workflow_def"
                    " WHERE tenant_id=CAST(:t AS uuid) AND wf_key=:k"
                    " ORDER BY version DESC LIMIT 1"
                ),
                {"t": tenant_id, "k": wf_key},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def get_def(
        self, *, tenant_id: str, wf_key: str, version: int
    ) -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT * FROM public.sys_workflow_def"
                    " WHERE tenant_id=CAST(:t AS uuid) AND wf_key=:k AND version=:v"
                ),
                {"t": tenant_id, "k": wf_key, "v": version},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def list_latest(
        self, *, tenant_id: str, user_id: str = "", team_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """032:归属过滤(user_id 非空时:创建者 或 归属用户所在团队;
        空=全量[管理面调度等内部调用])。"""
        params: dict[str, Any] = {"t": tenant_id}
        if user_id:
            params["u"] = user_id
            params["tids"] = team_ids or []
            sql = (
                "SELECT DISTINCT ON (wf_key) * FROM public.sys_workflow_def"
                " WHERE tenant_id=CAST(:t AS uuid)"
                " AND (created_by=CAST(:u AS uuid) OR team_id = ANY(CAST(:tids AS uuid[])))"
                " ORDER BY wf_key, version DESC"
            )
        else:
            sql = (
                "SELECT DISTINCT ON (wf_key) * FROM public.sys_workflow_def"
                " WHERE tenant_id=CAST(:t AS uuid)"
                " ORDER BY wf_key, version DESC"
            )
        rows = (await self._s.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]

    async def list_versions(self, *, tenant_id: str, wf_key: str) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, wf_key, name, version, enabled, created_at"
                    " FROM public.sys_workflow_def"
                    " WHERE tenant_id=CAST(:t AS uuid) AND wf_key=:k"
                    " ORDER BY version DESC"
                ),
                {"t": tenant_id, "k": wf_key},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def set_enabled(self, *, def_id: str, tenant_id: str, enabled: bool) -> bool:
        res = cast(
            "CursorResult[object]",  # DML 结果为 CursorResult(chat 仓储同款)
            await self._s.execute(
                text(
                    "UPDATE public.sys_workflow_def SET enabled=:e"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"e": enabled, "i": def_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def delete_key(self, *, tenant_id: str, wf_key: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "DELETE FROM public.sys_workflow_def"
                    " WHERE tenant_id=CAST(:t AS uuid) AND wf_key=:k"
                ),
                {"t": tenant_id, "k": wf_key},
            ),
        )
        return res.rowcount > 0

    async def insert_run(
        self,
        *,
        tenant_id: str,
        wf_key: str,
        version: int,
        trigger_source: str,
        inputs: dict[str, Any],
        status: str,
        node_runs: list[dict[str, Any]],
        error: str,
        duration_ms: int,
    ) -> str:
        rid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_workflow_run"
                " (id, tenant_id, wf_key, version, trigger_source, inputs,"
                "  status, node_runs, error, duration_ms)"
                " VALUES (:i, CAST(:t AS uuid), :k, :v, :ts,"
                "         CAST(:inp AS jsonb), :st, CAST(:nr AS jsonb), :e, :d)"
            ),
            {
                "i": rid,
                "t": tenant_id,
                "k": wf_key,
                "v": version,
                "ts": trigger_source,
                "inp": json.dumps(inputs, ensure_ascii=False, default=str),
                "st": status,
                "nr": json.dumps(node_runs, ensure_ascii=False, default=str),
                "e": error[:2000],
                "d": duration_ms,
            },
        )
        return rid

    async def page_runs(
        self, *, tenant_id: str, wf_key: str, page: int, size: int
    ) -> tuple[int, list[dict[str, Any]]]:
        total = int(
            (
                await self._s.execute(
                    text(
                        "SELECT count(*) FROM public.sys_workflow_run"
                        " WHERE tenant_id=CAST(:t AS uuid) AND wf_key=:k"
                    ),
                    {"t": tenant_id, "k": wf_key},
                )
            ).scalar_one()
        )
        # R1-提示3:列表剥 node_runs.outputs(快照可含 64KB body/千行,
        # 全量回传会膨胀到数十 MB);status/error/duration 保留
        rows = (
            await self._s.execute(
                text(
                    "SELECT id, tenant_id, wf_key, version, trigger_source, inputs,"
                    " status, error, duration_ms, created_at,"
                    " (SELECT jsonb_agg(jsonb_set(e, '{outputs}', 'null'::jsonb))"
                    "  FROM jsonb_array_elements(node_runs) e) AS node_runs"
                    " FROM public.sys_workflow_run"
                    " WHERE tenant_id=CAST(:t AS uuid) AND wf_key=:k"
                    " ORDER BY created_at DESC, id DESC LIMIT :lim OFFSET :off"
                ),
                {"t": tenant_id, "k": wf_key, "lim": size, "off": (page - 1) * size},
            )
        ).mappings().all()
        return total, [dict(r) for r in rows]

    async def commit(self) -> None:
        await self._s.commit()
