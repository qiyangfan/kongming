"""report 仓储(088):report / report_version / report_section SQL。

隔离铁律:一切读写强制 tenant_id+user_id 双过滤;归属收口与 chat 404 同形
(不存在/越权/已删除不区分)。版本不可变:版本与章节一经插入永不 UPDATE
(编辑/刷新恒插入新版本);终态守卫由 finish_generation 的条件 UPDATE 兑现。
"""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.db import uuid7
from kongming.report.models import SectionNode


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class SqlReportRepo:
    def __init__(self, s: AsyncSession) -> None:
        self._s = s

    async def insert_report(
        self,
        *,
        report_id: str,
        tenant_id: str,
        user_id: str,
        session_id: str | None,
        title: str,
        datasource_key: str,
        instruction: str,
        trace_id: str | None,
    ) -> str:
        await self._s.execute(
            text(
                "INSERT INTO public.report"
                " (id, tenant_id, user_id, session_id, title, status, phase,"
                "  datasource_key, instruction, trace_id)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:u AS uuid), CAST(:s AS uuid),"
                "         :ti, 'GENERATING', 'PLANNING', :ds, :in, :tr)"
            ),
            {
                "i": report_id,
                "t": tenant_id,
                "u": user_id,
                "s": session_id,
                "ti": title,
                "ds": datasource_key,
                "in": instruction,
                "tr": trace_id,
            },
        )
        return report_id

    async def get_report(
        self, *, report_id: str, tenant_id: str, user_id: str
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_id, user_id, session_id, title, status, phase,"
                        "       datasource_key, version, instruction, fail_reason,"
                        "       usage_summary, trace_id, created_at, updated_at, deleted_at"
                        " FROM public.report"
                        " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                        "   AND user_id = CAST(:u AS uuid)"
                    ),
                    {"i": report_id, "t": tenant_id, "u": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def set_phase(self, *, report_id: str, phase: str) -> None:
        await self._s.execute(
            text(
                "UPDATE public.report SET phase = :p, updated_at = now()"
                " WHERE id = CAST(:i AS uuid) AND status = 'GENERATING'"
            ),
            {"p": phase, "i": report_id},
        )

    async def mark_interrupted_failed(self) -> int:
        """启动清扫:进程中断时滞留 GENERATING 的报告收敛 FAILED
        (与 task 域重启扫描同款口径:不静默悬挂;GENERATING 是唯一活动态,
        进程死后无人在途可推进它)。返回标记行数。"""
        result = await self._s.execute(
            text(
                "UPDATE public.report"
                " SET status = 'FAILED', phase = 'DONE', fail_reason = :fr, updated_at = now()"
                " WHERE status = 'GENERATING' RETURNING id"
            ),
            {"fr": "进程中断,生成未完成(可重新发起)"},
        )
        return len(result.fetchall())

    async def finish_generation(
        self,
        *,
        report_id: str,
        status: str,
        fail_reason: str | None,
        usage_summary: dict[str, object] | None,
        version: int | None,
        version_meta: dict[str, object] | None,
        data_time_point: datetime,
        created_by: str,
        sections: list[SectionNode],
    ) -> None:
        """终态落库(调用方管理事务):仅当行仍 GENERATING 才生效——
        生成期间被软删的竞态在此收口(不复活、不写版本)。"""
        hit = (
            await self._s.execute(
                text(
                    "UPDATE public.report"
                    " SET status = :st, phase = 'DONE', fail_reason = :fr,"
                    "     usage_summary = CAST(:us AS jsonb), updated_at = now()"
                    " WHERE id = CAST(:i AS uuid) AND status = 'GENERATING'"
                    " RETURNING id"
                ),
                {
                    "st": status,
                    "fr": fail_reason,
                    "us": _dump(usage_summary) if usage_summary is not None else None,
                    "i": report_id,
                },
            )
        ).scalar_one_or_none()
        if hit is None:
            return
        if version is not None:
            await self.insert_version_with_sections(
                report_id=report_id,
                version=version,
                meta=version_meta or {},
                data_time_point=data_time_point,
                created_by=created_by,
                sections=sections,
            )
            await self._s.execute(
                text(
                    "UPDATE public.report SET version = :v, updated_at = now()"
                    " WHERE id = CAST(:i AS uuid)"
                ),
                {"v": version, "i": report_id},
            )

    async def claim_next_version(
        self, *, report_id: str, tenant_id: str, user_id: str, expected_version: int
    ) -> int | None:
        """原子申领下一版本号(刷新路径):带期望版本的 CAS——读数后若被编辑
        推进则申领失败返回 None(审查 P1-4:无 CAS 时旧内容可覆盖最新版本)。"""
        row = (
            await self._s.execute(
                text(
                    "UPDATE public.report SET version = version + 1, updated_at = now()"
                    " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                    "   AND user_id = CAST(:u AS uuid)"
                    "   AND status IN ('READY','PARTIAL') AND version = :ev"
                    " RETURNING version"
                ),
                {"i": report_id, "t": tenant_id, "u": user_id, "ev": expected_version},
            )
        ).scalar_one_or_none()
        return int(row) if row is not None else None

    async def cas_version(
        self, *, report_id: str, tenant_id: str, user_id: str, expected_version: int
    ) -> int | None:
        """乐观锁(编辑路径):当前版本=expected 且状态可编辑才推进 expected+1。"""
        row = (
            await self._s.execute(
                text(
                    "UPDATE public.report SET version = version + 1, updated_at = now()"
                    " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                    "   AND user_id = CAST(:u AS uuid)"
                    "   AND status IN ('READY','PARTIAL') AND version = :ev"
                    " RETURNING version"
                ),
                {"i": report_id, "t": tenant_id, "u": user_id, "ev": expected_version},
            )
        ).scalar_one_or_none()
        return int(row) if row is not None else None

    async def insert_version_with_sections(
        self,
        *,
        report_id: str,
        version: int,
        meta: dict[str, object],
        data_time_point: datetime,
        created_by: str,
        sections: list[SectionNode],
    ) -> None:
        vid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.report_version"
                " (id, report_id, version, meta, data_time_point, created_by)"
                " VALUES (:i, CAST(:r AS uuid), :v, CAST(:m AS jsonb), :dtp, CAST(:cb AS uuid))"
            ),
            {
                "i": vid,
                "r": report_id,
                "v": version,
                "m": _dump(meta),
                "dtp": data_time_point,
                "cb": created_by,
            },
        )
        # 按输入序插入(validate_tree 已保证父先于子;按 ord 重排会让
        # 「ord 与列表序不一致的合法载荷」在校验通过后仍被拒绝);
        # 行 id 与内存 section_id 解耦:child 引用经 id_map 翻译为库内行 id。
        id_map: dict[str | None, str] = {}
        for n in sections:
            parent_row = id_map.get(n.parent_id)
            if n.parent_id is not None and parent_row is None:
                raise ValueError(f"章节父节点未先出现: {n.parent_id}")
            sid = str(uuid7())
            if n.section_id:
                id_map[n.section_id] = sid
            await self._insert_section(
                version_id=vid, section_id=sid, parent_row_id=parent_row, n=n
            )

    async def _insert_section(
        self, *, version_id: str, section_id: str, parent_row_id: str | None, n: SectionNode
    ) -> None:
        bindings = [
            {
                "sql": b.sql,
                "status": b.status,
                "columns": b.columns,
                "rows": b.rows,
                "row_count": b.row_count,
                "truncated": b.truncated,
                "fail_reason": b.fail_reason,
            }
            for b in n.bindings
        ]
        await self._s.execute(
            text(
                "INSERT INTO public.report_section"
                " (id, report_version_id, parent_id, title, narrative_md, bindings,"
                "  chart, ord, data_status, fail_reason)"
                " VALUES (:i, CAST(:v AS uuid), CAST(:p AS uuid), :t, :n, CAST(:b AS jsonb),"
                "         CAST(:c AS jsonb), :o, :ds, :fr)"
            ),
            {
                "i": section_id,
                "v": version_id,
                "p": parent_row_id,
                "t": n.title,
                "n": n.narrative_md,
                "b": _dump(bindings),
                "c": _dump(n.chart) if n.chart is not None else None,
                "o": n.ord,
                "ds": n.data_status,
                "fr": n.fail_reason,
            },
        )

    async def get_version_meta(self, *, report_id: str, version: int) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, report_id, version, meta, data_time_point,"
                        "       created_by, created_at"
                        " FROM public.report_version"
                        " WHERE report_id = CAST(:r AS uuid) AND version = :v"
                    ),
                    {"r": report_id, "v": version},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def get_sections(self, *, version_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, parent_id, title, narrative_md, bindings, chart,"
                        "       ord, data_status, fail_reason"
                        " FROM public.report_section"
                        " WHERE report_version_id = CAST(:v AS uuid)"
                        " ORDER BY ord"
                    ),
                    {"v": version_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_versions(self, *, report_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, version, meta, data_time_point, created_at"
                        " FROM public.report_version"
                        " WHERE report_id = CAST(:r AS uuid)"
                        " ORDER BY version DESC"
                    ),
                    {"r": report_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_reports(
        self, *, tenant_id: str, user_id: str, page: int, size: int
    ) -> tuple[list[dict[str, Any]], int]:
        where = (
            " WHERE tenant_id = CAST(:t AS uuid) AND user_id = CAST(:u AS uuid)"
            "   AND status != 'DELETED'"
        )
        total = int(
            (
                await self._s.execute(
                    text(f"SELECT count(*) FROM public.report{where}"),  # noqa: S608 —— where 为常量片段
                    {"t": tenant_id, "u": user_id},
                )
            ).scalar_one()
        )
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, title, status, phase, datasource_key, version,"  # noqa: S608 —— where 为常量片段
                        "       created_at, updated_at"
                        " FROM public.report"
                        f"{where}"
                        " ORDER BY created_at DESC LIMIT :lim OFFSET :off"
                    ),
                    {"t": tenant_id, "u": user_id, "lim": size, "off": (page - 1) * size},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows], total

    async def soft_delete(self, *, report_id: str, tenant_id: str, user_id: str) -> bool:
        hit = (
            await self._s.execute(
                text(
                    "UPDATE public.report"
                    " SET status = 'DELETED', deleted_at = now(), updated_at = now()"
                    " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t AS uuid)"
                    "   AND user_id = CAST(:u AS uuid) AND status != 'DELETED'"
                    " RETURNING id"
                ),
                {"i": report_id, "t": tenant_id, "u": user_id},
            )
        ).scalar_one_or_none()
        return hit is not None

    async def rename(self, *, report_id: str, tenant_id: str, user_id: str, title: str) -> None:
        await self._s.execute(
            text(
                "UPDATE public.report SET title = :t, updated_at = now()"
                " WHERE id = CAST(:i AS uuid) AND tenant_id = CAST(:t2 AS uuid)"
                "   AND user_id = CAST(:u AS uuid) AND status IN ('READY','PARTIAL')"
            ),
            {"t": title, "i": report_id, "t2": tenant_id, "u": user_id},
        )

    async def set_status_editable(self, *, report_id: str, status: str) -> None:
        await self._s.execute(
            text(
                "UPDATE public.report SET status = :st, updated_at = now()"
                " WHERE id = CAST(:i AS uuid) AND status IN ('READY','PARTIAL')"
            ),
            {"st": status, "i": report_id},
        )
