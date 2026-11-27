"""报告生成、版本管理、刷新、导出和删除用例。取数始终经受控 SQL 执行端点,
状态迁移由领域模型校验。"""

import logging
from datetime import UTC, datetime
from typing import Any, cast

from kongming.report import models as m
from kongming.report.models import Binding, SectionNode, section_from_row, sections_from_payload
from kongming.report.ports import (
    AgentReportGateway,
    ChatCardWriter,
    OperLogPort,
    ReportRepo,
    SqlDefenseRunner,
)

log = logging.getLogger(__name__)

# 快照行数上限(刷新路径;生成路径由 agent 侧同口径截断)
SNAPSHOT_ROW_LIMIT = 500


class ReportConflict(Exception):
    """乐观锁冲突/状态不允许该操作(调用方映射 409)。"""


class ReportNotFound(Exception):
    """不存在/越权/已删除(404 同形,不泄露存在性)。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _apply_section_edits(
    nodes: list[SectionNode], section_edits: list[dict[str, object]]
) -> tuple[list[SectionNode], int]:
    """按 section_id 覆盖叙述/标题,其余章节原样保留;返回 (章节树, 生效编辑数)。

    越界章节或字段即 ValueError。"""
    edits = {str(e.get("section_id")): e for e in section_edits if e.get("section_id")}
    known = {n.section_id for n in nodes}
    unknown = set(edits) - known
    if unknown:
        raise ValueError(f"编辑目标章节不存在: {sorted(unknown)}")
    edited: list[SectionNode] = []
    for n in nodes:
        e = edits.get(n.section_id or "")
        if e is None:
            edited.append(n)
            continue
        unknown = set(e) - {"section_id", "title", "narrative_md"}
        if unknown:
            raise ValueError(f"编辑字段越界: {sorted(unknown)}")
        edited.append(
            SectionNode(
                title=str(e.get("title") or n.title),
                narrative_md=str(
                    e.get("narrative_md") if e.get("narrative_md") is not None else n.narrative_md
                ),
                bindings=n.bindings,
                chart=n.chart,
                section_id=n.section_id,
                parent_id=n.parent_id,
                ord=n.ord,
                data_status=n.data_status,
                fail_reason=n.fail_reason,
            )
        )
    return edited, len(edits)


class ReportService:
    def __init__(
        self,
        repo: ReportRepo,
        agent: AgentReportGateway,
        runner: SqlDefenseRunner,
        operlog: OperLogPort,
        cards: ChatCardWriter,
    ) -> None:
        self._repo = repo
        self._agent = agent
        self._runner = runner
        self._operlog = operlog
        self._cards = cards

    # ── 生成 ──

    async def create_generation(
        self,
        *,
        report_id: str,
        tenant_id: str,
        user_id: str,
        title: str,
        instruction: str,
        datasource_key: str,
        session_id: str | None,
        trace_id: str,
    ) -> str:
        """创建 GENERATING 行(实际派发由 router 后台任务驱动 run_generation)。"""
        return await self._repo.insert_report(
            report_id=report_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            title=title,
            datasource_key=datasource_key,
            instruction=instruction,
            trace_id=trace_id,
        )

    async def run_generation(
        self,
        *,
        report: dict[str, Any],
        tenant_key: str,
        context_digest: str,
    ) -> dict[str, object] | None:
        """后台任务:回调 agent 编排并落终态(由调用方提交)。agent 不可达/
        未产出章节 → FAILED(原因摘要,已产出章节不虚构)。返回报告卡载荷
        (终态提交后由调用方发送;None=无卡)。"""
        report_id = str(report["id"])
        payload: dict[str, object] = {
            "report_id": report_id,
            "trace_id": str(report.get("trace_id") or ""),
            "tenant_key": tenant_key,
            "datasource_key": str(report.get("datasource_key") or "default"),
            "user_id": str(report["user_id"]),
            "instruction": str(report.get("instruction") or ""),
            "title": str(report.get("title") or ""),
        }
        if context_digest:
            payload["context_digest"] = context_digest
        result = await self._agent.generate_report(payload)
        usage = _usage_of(result)
        if result is None or not isinstance(result.get("sections"), list) or not result["sections"]:
            await self._repo.finish_generation(
                report_id=report_id,
                status=m.STATUS_FAILED,
                fail_reason=(
                    str(result.get("error") or "生成编排未产出章节")
                    if result is not None
                    else "生成编排不可用"
                ),
                usage_summary=usage,
                version=None,
                version_meta=None,
                data_time_point=_now(),
                created_by=str(report["user_id"]),
                sections=[],
            )
            _report_report_event(report, status=m.STATUS_FAILED, sections=0)
            return None
        sections = sections_from_payload(result["sections"])
        m.validate_tree(sections)
        # 预算耗尽/缺数章节如实判定终态:全完成→READY,否则 PARTIAL(不谎称完成)
        budget_exhausted = bool(result.get("partial"))
        status = (
            m.STATUS_READY
            if (not budget_exhausted and m.all_sections_complete(sections))
            else m.STATUS_PARTIAL
        )
        await self._repo.finish_generation(
            report_id=report_id,
            status=status,
            fail_reason=str(result["error"]) if result.get("error") else None,
            usage_summary=usage,
            version=1,
            version_meta={
                "instruction": str(report.get("instruction") or ""),
                "entry": "session" if report.get("session_id") else "direct",
                "section_count": len(sections),
            },
            data_time_point=_now(),
            created_by=str(report["user_id"]),
            sections=sections,
        )
        _report_report_event(report, status=status, sections=len(sections))
        session_id = report.get("session_id")
        if session_id and status in (m.STATUS_READY, m.STATUS_PARTIAL):
            # 报告卡挂回发起会话(failed 不挂——会话内只呈现成品/部分成品);
            # 返回载荷由调用方在终态事务提交后发送(卡不得先于终态可见)
            return {
                "session_id": str(session_id),
                "tenant_id": str(report["tenant_id"]),
                "report_id": report_id,
                "version": 1,
                "status": status,
                "title": str(report.get("title") or ""),
                "summary": _card_summary(status, sections, str(result.get("error") or "")),
                "trace_id": cast("str | None", report.get("trace_id")),
            }
        return None

    async def emit_card(self, card: dict[str, object]) -> None:
        """报告卡发送(调用方在终态事务提交后调用——卡不得先于终态可见)。"""
        await self._cards.append_report_card(
            session_id=str(card["session_id"]),
            tenant_id=str(card["tenant_id"]),
            report_id=str(card["report_id"]),
            version=int(str(card["version"])),
            status=str(card["status"]),
            title=str(card["title"]),
            summary=str(card["summary"]),
            trace_id=cast("str | None", card.get("trace_id")),
        )

    # ── 查询 ──

    async def get_detail(
        self,
        *,
        report_id: str,
        tenant_id: str,
        user_id: str,
        version: int | None = None,
    ) -> dict[str, Any]:
        report = await self._require(report_id=report_id, tenant_id=tenant_id, user_id=user_id)
        v = version if version is not None else int(report["version"])
        vmeta = await self._repo.get_version_meta(report_id=report_id, version=v) if v > 0 else None
        sections: list[dict[str, Any]] = []
        if vmeta is not None:
            sections = await self._repo.get_sections(version_id=str(vmeta["id"]))
        return {"report": report, "version_meta": vmeta, "sections": sections}

    async def get_progress(self, *, report_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
        """生成进度轻量读(轮询单源):只读报告行状态,不加载版本元数据与
        章节全文——进度轮询高频,每次拉全量章节属纯浪费。"""
        report = await self._require(report_id=report_id, tenant_id=tenant_id, user_id=user_id)
        return {
            "report_id": str(report["id"]),
            "status": str(report["status"]),
            "phase": str(report["phase"]),
        }

    async def list_versions(
        self, *, report_id: str, tenant_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        await self._require(report_id=report_id, tenant_id=tenant_id, user_id=user_id)
        return await self._repo.list_versions(report_id=report_id)

    async def list_reports(
        self, *, tenant_id: str, user_id: str, page: int, size: int
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list_reports(
            tenant_id=tenant_id, user_id=user_id, page=page, size=size
        )

    # ── 编辑(乐观锁,恒产生新版本)──

    async def save_edit(
        self,
        *,
        report_id: str,
        tenant_id: str,
        user_id: str,
        base_version: int,
        section_edits: list[dict[str, object]],
        title: str | None = None,
    ) -> int:
        """编辑保存:仅叙述/标题可改(绑定与图表随版本只读复制,数据以刷新
        路径演进,叙述与数据不脱钩);基于 base_version 乐观锁,冲突拒绝。"""
        report = await self._require(report_id=report_id, tenant_id=tenant_id, user_id=user_id)
        if report["status"] not in m.EDITABLE_STATUSES:
            raise ReportConflict(f"状态 {report['status']} 不可编辑")
        # base_version 预检前置(authoritative CAS 仍在末尾):基于旧版本的提交
        # 一律 409(可行动的错误),不让章节 id 失配的 422 抢占语义
        if base_version != int(report["version"]):
            raise ReportConflict("版本已演进,请基于最新版本编辑")
        if not section_edits and not (title and title.strip()):
            raise ValueError("编辑内容为空(需至少提供标题或一个章节修改)")
        vmeta = await self._repo.get_version_meta(
            report_id=report_id, version=int(report["version"])
        )
        if vmeta is None:
            raise ReportConflict("尚无可编辑版本")
        nodes = [
            section_from_row(r) for r in await self._repo.get_sections(version_id=str(vmeta["id"]))
        ]
        edited, edit_count = _apply_section_edits(nodes, section_edits)
        m.validate_tree(edited)
        new_version = await self._repo.cas_version(
            report_id=report_id,
            tenant_id=tenant_id,
            user_id=user_id,
            expected_version=base_version,
        )
        if new_version is None:
            raise ReportConflict("版本已演进,请基于最新版本编辑")
        if title:
            await self._repo.rename(
                report_id=report_id, tenant_id=tenant_id, user_id=user_id, title=title
            )
        dtp = vmeta.get("data_time_point")
        await self._repo.insert_version_with_sections(
            report_id=report_id,
            version=new_version,
            meta={"edit_base_version": base_version, "edited_sections": edit_count},
            # 快照原样复制,数据时点继承基线(编辑未重取数,时点≠编辑时刻)
            data_time_point=dtp if isinstance(dtp, datetime) else _now(),
            created_by=user_id,
            sections=edited,
        )
        return new_version

    # ── 刷新数据(重执行绑定快照 → 新版本)──

    async def refresh_data(
        self,
        *,
        report_id: str,
        tenant_id: str,
        tenant_key: str,
        user_id: str,
        trace_id: str,
    ) -> int:
        report = await self._require(report_id=report_id, tenant_id=tenant_id, user_id=user_id)
        if report["status"] not in (m.STATUS_READY, m.STATUS_PARTIAL):
            raise ReportConflict(f"状态 {report['status']} 不可刷新")
        current = int(report["version"])
        vmeta = await self._repo.get_version_meta(report_id=report_id, version=current)
        if vmeta is None:
            raise ReportConflict("尚无可刷新版本")
        nodes = [
            section_from_row(r) for r in await self._repo.get_sections(version_id=str(vmeta["id"]))
        ]
        # 以最新版本的叶子绑定重执行(全量防御,与 rerun 同源);失败章节如实 MISSING
        refreshed = [
            await self._refresh_section(
                n, report=report, trace_id=trace_id, tenant_key=tenant_key, user_id=user_id
            )
            for n in nodes
        ]
        m.validate_tree(refreshed)
        new_version = await self._repo.claim_next_version(
            report_id=report_id,
            tenant_id=tenant_id,
            user_id=user_id,
            expected_version=current,
        )
        if new_version is None:
            raise ReportConflict("版本已演进,请基于最新版本刷新")
        await self._repo.insert_version_with_sections(
            report_id=report_id,
            version=new_version,
            meta={"refresh_of_version": current, "section_count": len(refreshed)},
            data_time_point=_now(),
            created_by=user_id,
            sections=refreshed,
        )
        # 刷新后全完成则回升 READY(仍缺数维持 PARTIAL——不谎称完成)
        await self._repo.set_status_editable(
            report_id=report_id,
            status=m.STATUS_READY if m.all_sections_complete(refreshed) else m.STATUS_PARTIAL,
        )
        return new_version

    async def _refresh_section(
        self,
        n: SectionNode,
        *,
        report: dict[str, Any],
        trace_id: str,
        tenant_key: str,
        user_id: str,
    ) -> SectionNode:
        """重执行章节绑定;失败绑定如实记 MISSING,章节状态随绑定汇总。"""
        if not n.bindings:
            return n
        new_bindings = [
            await self._refresh_binding(
                b, report=report, trace_id=trace_id, tenant_key=tenant_key, user_id=user_id
            )
            for b in n.bindings
        ]
        reasons = [b.fail_reason for b in new_bindings if b.status == m.DATA_MISSING]
        return SectionNode(
            title=n.title,
            narrative_md=n.narrative_md,
            bindings=new_bindings,
            chart=n.chart,
            section_id=n.section_id,
            parent_id=n.parent_id,
            ord=n.ord,
            data_status=m.DATA_MISSING if reasons else m.DATA_OK,
            fail_reason=reasons[-1] if reasons else None,  # 与逐条覆盖口径一致
        )

    async def _refresh_binding(
        self,
        b: Binding,
        *,
        report: dict[str, Any],
        trace_id: str,
        tenant_key: str,
        user_id: str,
    ) -> Binding:
        """重执行单个绑定(截断上限与执行端 limit=cap+1 探针保持一致)。"""
        verdict, columns, rows, reason = await self._runner.run(
            trace_id=trace_id,
            tenant_key=tenant_key,
            datasource_key=str(report["datasource_key"]),
            sql_text=b.sql,
            user_id=user_id,
        )
        if verdict != "PASS":
            return Binding(sql=b.sql, status=m.DATA_MISSING, fail_reason=reason or verdict)
        total = len(rows)  # 执行端 limit=cap+1:len>cap 即超限
        kept = rows[:SNAPSHOT_ROW_LIMIT]
        return Binding(
            sql=b.sql,
            status=m.DATA_OK,
            columns=columns,
            rows=kept,
            row_count=total,
            truncated=total > SNAPSHOT_ROW_LIMIT,
        )

    # ── 删除/导出 ──

    async def delete(self, *, report_id: str, tenant_id: str, user_id: str) -> None:
        await self._require(report_id=report_id, tenant_id=tenant_id, user_id=user_id)
        await self._repo.soft_delete(report_id=report_id, tenant_id=tenant_id, user_id=user_id)

    async def export(
        self, *, report_id: str, tenant_id: str, user_id: str, version: int | None
    ) -> str:
        detail = await self.get_detail(
            report_id=report_id, tenant_id=tenant_id, user_id=user_id, version=version
        )
        report = detail["report"]
        vmeta = detail["version_meta"]
        if vmeta is None:
            raise ReportConflict("尚无可导出版本")
        nodes = [section_from_row(r) for r in detail["sections"]]
        dtp = vmeta.get("data_time_point")
        md = m.export_markdown(
            title=str(report["title"]),
            status=str(report["status"]),
            version=int(vmeta["version"]),
            data_time_point=dtp.isoformat() if isinstance(dtp, datetime) else str(dtp),
            sections=nodes,
        )
        await self._operlog.add(
            user_id,
            "report.export",
            report_id,
            {"version": int(vmeta["version"]), "status": str(report["status"])},
        )
        return md

    # ── 内部 ──

    async def _require(self, *, report_id: str, tenant_id: str, user_id: str) -> dict[str, Any]:
        report = await self._repo.get_report(
            report_id=report_id, tenant_id=tenant_id, user_id=user_id
        )
        if report is None or report["status"] == m.STATUS_DELETED:
            raise ReportNotFound
        return report


def _usage_of(result: dict[str, object] | None) -> dict[str, object] | None:
    if not result:
        return None
    usage = result.get("usage")
    return cast("dict[str, object]", usage) if isinstance(usage, dict) else None


def _card_summary(status: str, sections: list[SectionNode], error: str) -> str:
    parent_ids = {n.parent_id for n in sections if n.parent_id is not None}
    leaves = [n for n in sections if n.section_id not in parent_ids] or sections
    total = len(leaves)
    ok = sum(1 for n in leaves if n.data_status == m.DATA_OK and not n.fail_reason)
    if status == m.STATUS_READY:
        return f"报告已生成:全部 {total} 个章节完成"
    if error:
        return f"报告部分完成:{ok}/{total} 个章节完成;{error}"
    return f"报告部分完成:{ok}/{total} 个章节完成,其余如实标注未完成"


def _report_report_event(report: dict[str, Any], *, status: str, sections: int) -> None:
    from kongming.control.events import report_product_event

    report_product_event(
        "report.generation",
        tenant_id=str(report["tenant_id"]),
        user_id=str(report["user_id"]),
        attributes={"status": status, "section_count": sections},
    )
