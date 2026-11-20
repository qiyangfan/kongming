"""Agent 侧报告生成编排:取数经控制面安全执行端点;规划、章节撰写与组装
受 LLM 调用数、token 与耗时预算约束,耗尽时保留未完成章节并如实标记。"""

import contextvars
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from kongming_agent import report_prompts as rp
from kongming_agent.apiclient import SchemaMeta, ToolExecuteOutcome
from kongming_agent.llm import LlmError, reset_usage_collector, set_usage_collector
from kongming_agent.settings import Settings

log = logging.getLogger(__name__)

# 规划规模护栏:章数在此截断;叶数由单章上限 × 章数隐式约束,
# 总量由撰写/修复调用次数上限兜底
_MAX_CHAPTERS = 8
_SCHEMA_DIGEST_MAX_CHARS = 8000
_CHART_TYPES = frozenset({"line", "bar", "pie", "scatter"})

_PHASES = ("PLANNING", "FETCHING", "WRITING", "ASSEMBLING")


class ReportLlmPort(Protocol):
    """编排对 LLM 的最小依赖面(LlmClient 与测试 fake 结构性满足)。"""

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        trace_id: str | None = None,
        tenant_key: str | None = None,
    ) -> str: ...


class ReportApiPort(Protocol):
    """编排对控制面回调的最小依赖面(ApiClient 与测试 fake 结构性满足)。"""

    async def fetch_schema(
        self, *, tenant_key: str, datasource_key: str = "default"
    ) -> SchemaMeta | None: ...

    async def tool_execute(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        user_id: str | None,
        tool_name: str,
        args: dict[str, object],
        channel: str = "chat",
    ) -> ToolExecuteOutcome: ...

    async def report_progress(
        self, *, report_id: str, phase: str, tenant_key: str = "default"
    ) -> None: ...


@dataclass
class _Budget:
    """报告级预算三约束(编排内判定;耗尽即停,PARTIAL 收口)。"""

    max_calls: int
    max_tokens: int
    max_seconds: float
    calls: int = 0
    started: float = field(default_factory=time.monotonic)

    def spend_call(self) -> bool:
        """记账一次 LLM 调用;False=预算已尽,调用不得发起。"""
        if self.calls >= self.max_calls:
            return False
        self.calls += 1
        return True

    def exhausted(self, tokens: int) -> bool:
        return (
            self.calls >= self.max_calls
            or tokens >= self.max_tokens
            or (time.monotonic() - self.started) >= self.max_seconds
        )

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


@dataclass
class _Leaf:
    leaf_id: str
    title: str
    sql: str
    chapter_id: str
    chart: dict[str, object] | None = None
    # 取数结果(全部就绪后进 sections 组装)
    columns: list[str] = field(default_factory=list[str])
    rows: list[list[object]] = field(default_factory=list[list[object]])
    status: str = "PENDING"  # OK / MISSING / PENDING
    fail_reason: str | None = None
    truncated: bool = False
    row_count: int = 0  # 截断前总行数(工具通道如实返回的行数,契约语义)


def _extract_json(raw: str) -> dict[str, Any]:
    """LLM 输出 → JSON 对象(剥代码围栏 + 首尾大括号截取;畸形抛 ValueError)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("plan 输出无 JSON 对象")
    obj = json.loads(text[start : end + 1])
    if not isinstance(obj, dict):
        raise TypeError("plan 输出非对象")
    return cast("dict[str, Any]", obj)


def _schema_digest(meta: SchemaMeta | None) -> str:
    """SchemaMeta → 规划段文本(总量截断,防 prompt 膨胀;None=内省不可用)。"""
    if meta is None:
        return "(schema 内省不可用:请基于指令常识规划,取数失败章节将被如实标注)"
    lines: list[str] = []
    total = 0
    for t in meta.tables:
        cols = ", ".join(
            f"{c.name} {c.data_type}" + (f"({c.comment})" if c.comment else "") for c in t.columns
        )
        line = f"- {t.name}" + (f" —— {t.comment}" if t.comment else "") + f": {cols}"
        if total + len(line) > _SCHEMA_DIGEST_MAX_CHARS:
            lines.append("(表清单已截断)")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _parse_chart(raw: object) -> dict[str, object] | None:
    """图表声明 → 收敛后的 chart 字段;类型/坐标不完整视为无图。"""
    if not isinstance(raw, dict):
        return None
    chartd = cast("dict[str, Any]", raw)
    if str(chartd.get("type")) in _CHART_TYPES and chartd.get("x") and chartd.get("y"):
        return {
            "type": str(chartd.get("type")),
            "x": str(chartd.get("x")),
            "y": str(chartd.get("y")),
        }
    return None


def _parse_leaf(lf: object, ch_id: str, j: int, seen: set[str]) -> _Leaf:
    """叶子条目校验与收敛(id 唯一、SQL 必填);畸形直接失败。"""
    if not isinstance(lf, dict):
        raise TypeError("plan 叶子条目畸形")
    lfd = cast("dict[str, Any]", lf)
    leaf_id = str(lfd.get("id") or f"{ch_id}-{j}")
    if leaf_id in seen:
        raise ValueError(f"叶子 id 重复: {leaf_id}")
    seen.add(leaf_id)
    sql = str(lfd.get("sql") or "").strip()
    if not sql:
        raise ValueError(f"叶子缺 SQL: {leaf_id}")
    return _Leaf(
        leaf_id=leaf_id,
        title=str(lfd.get("title") or f"小节 {j + 1}"),
        sql=sql,
        chapter_id=ch_id,
        chart=_parse_chart(lfd.get("chart")),
    )


def _parse_chapter(ch: object, i: int, seen: set[str]) -> tuple[dict[str, Any], list[_Leaf]] | None:
    """章节条目校验与收敛;无有效叶子返回 None。"""
    if not isinstance(ch, dict):
        raise TypeError("plan 章节条目畸形")
    chd = cast("dict[str, Any]", ch)
    ch_id = str(chd.get("id") or f"c{i}")
    if ch_id in seen:  # id 重复直接失败(fail-closed)
        raise ValueError(f"章节 id 重复: {ch_id}")
    seen.add(ch_id)
    leaves: list[_Leaf] = []
    raw_children = chd.get("children")
    if isinstance(raw_children, list):
        for j, lf in enumerate(cast("list[object]", raw_children)):
            # 形态校验先于数量护栏:超限之后的畸形条目仍须 fail-closed
            if not isinstance(lf, dict):
                raise TypeError("plan 叶子条目畸形")
            if len(leaves) >= 6:  # 单章叶子护栏
                break
            leaves.append(_parse_leaf(cast("dict[str, object]", lf), ch_id, j, seen))
    if not leaves:
        return None
    return {"id": ch_id, "title": str(chd.get("title") or f"第 {i + 1} 章")}, leaves


def _flatten_plan(plan: dict[str, Any]) -> tuple[str, list[tuple[dict[str, Any], list[_Leaf]]]]:
    """plan 载荷 → (标题, [(章 dict, 叶列表)]);规模护栏与字段收敛在此收口。"""
    title = str(plan.get("title") or "").strip()
    raw_sections = plan.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ValueError("plan.sections 缺失或为空")
    chapters: list[tuple[dict[str, Any], list[_Leaf]]] = []
    seen: set[str] = set()
    for i, ch in enumerate(cast("list[object]", raw_sections[:_MAX_CHAPTERS])):
        chapter = _parse_chapter(ch, i, seen)
        if chapter is not None:
            chapters.append(chapter)
    if not chapters:
        raise ValueError("plan 无有效章节(章节必须含叶子)")
    return title, chapters


class ReportOrchestrator:
    """一次报告生成的编排器(每请求实例化;预算/进度/素材为其私有态)。"""

    def __init__(
        self,
        *,
        payload: dict[str, object],
        llm: ReportLlmPort,
        api: ReportApiPort,
        settings: Settings,
    ) -> None:
        self._payload = payload
        self._llm = llm
        self._api = api
        self._settings = settings
        self._budget = _Budget(
            max_calls=settings.report_max_llm_calls,
            max_tokens=settings.report_max_total_tokens,
            max_seconds=settings.report_max_seconds,
        )
        self._collector: list[dict[str, object]] = []
        self._partial = False
        self._stop_reason: str | None = None
        # 规划成功后的半成品态:中途失败时已产出章节随载荷保留
        # (spec「生成失败已产出章节保留供诊断」)
        self._title = ""
        self._chapters: list[tuple[dict[str, Any], list[_Leaf]]] | None = None
        self._narratives: dict[str, str] = {}

    # ── 对外入口 ──

    async def run(self) -> dict[str, object]:
        token: contextvars.Token[list[dict[str, object]] | None] = set_usage_collector(
            self._collector
        )
        try:
            return await self._run_inner()
        except LlmError as e:
            log.warning("报告生成 LLM 异常: %s", e)
            return self._degraded_payload(str(e))
        except (ValueError, KeyError) as e:
            log.warning("报告生成编排畸形: %s", e)
            return self._degraded_payload(f"生成编排异常: {e}")
        finally:
            reset_usage_collector(token)

    def _degraded_payload(self, message: str) -> dict[str, object]:
        """中途失败:规划已产出 → 部分收口(已取数章节保留,未撰写如实 PENDING);
        规划前失败 → 空错误载荷(api 侧 FAILED)。"""
        if self._chapters is None:
            return self._error_payload(message)
        self._partial = True
        self._stop_reason = self._stop_reason or message
        return self._assemble(
            title=self._title, chapters=self._chapters, narratives=self._narratives, error=message
        )

    # ── 管线 ──

    async def _plan(
        self,
        schema: SchemaMeta | None,
        instruction: str,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
    ) -> tuple[str, list[tuple[dict[str, Any], list[_Leaf]]]]:
        """调模型产出章节规划;载荷显式标题优先于模型标题。"""
        context = str(self._payload.get("context_digest") or "")
        context_block = (
            rp.CONTEXT_BLOCK_TEMPLATE.format(context_digest=context[:4000]) if context else ""
        )
        plan_raw = await self._llm.chat(
            rp.PLAN_SYSTEM_PROMPT,
            rp.PLAN_USER_TEMPLATE.format(
                instruction=instruction,
                datasource_key=datasource_key,
                schema_digest=_schema_digest(schema),
                context_block=context_block,
            ),
            temperature=0.2,
            trace_id=trace_id,
            tenant_key=tenant_key,
        )
        try:
            title, chapters = _flatten_plan(_extract_json(plan_raw))
        except TypeError as e:  # 结构类型不符(TypeError)归一为畸形,避免外层吞掉内部类型错误
            raise ValueError(f"plan 结构类型不符: {e}") from e
        if str(self._payload.get("title") or "").strip():
            title = str(self._payload["title"])  # 发起方显式标题优先
        return title, chapters

    async def _run_inner(self) -> dict[str, object]:
        report_id = str(self._payload.get("report_id") or "")
        trace_id = str(self._payload.get("trace_id") or "")
        tenant_key = str(self._payload.get("tenant_key") or "default")
        datasource_key = str(self._payload.get("datasource_key") or "default")
        user_id = cast("str | None", self._payload.get("user_id"))
        instruction = str(self._payload.get("instruction") or "")

        # 1) 规划
        await self._progress(report_id, "PLANNING")
        schema = await self._api.fetch_schema(tenant_key=tenant_key, datasource_key=datasource_key)
        if not self._budget.spend_call():
            return self._error_payload("预算耗尽:规划未能启动")
        title, chapters = await self._plan(
            schema, instruction, trace_id, tenant_key, datasource_key
        )
        self._title, self._chapters = title, chapters  # 半成品态留存(中途失败保章节)

        # 2) 逐叶取数(防御拒绝不绕道;语义修复至多一次,修正稿重过全量防御)
        await self._progress(report_id, "FETCHING")
        all_leaves = [lf for _, lvs in chapters for lf in lvs]
        for lf in all_leaves:
            if self._budget.exhausted(self._tokens()):
                self._mark_budget_stop(all_leaves)
                break
            await self._fetch_leaf(
                lf,
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                user_id=user_id,
                digest=_schema_digest(schema),
            )

        # 3) 逐章撰写(失败/未完成叶不由 LLM 撰写——确定性如实标注)
        await self._progress(report_id, "WRITING")
        for ch, lvs in chapters:
            if self._budget.exhausted(self._tokens()) or not self._budget.spend_call():
                self._partial = True
                self._stop_reason = self._stop_reason or "预算耗尽,部分章节未撰写"
                break
            self._narratives[str(ch["id"])] = await self._write_chapter(
                ch, lvs, trace_id, tenant_key
            )

        # 4) 组装
        await self._progress(report_id, "ASSEMBLING")
        return self._assemble(
            title=self._title,
            chapters=self._chapters or [],
            narratives=self._narratives,
            error=None,
        )

    # ── 取数(含一次语义修复)──

    async def _fetch_leaf(
        self,
        lf: _Leaf,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        user_id: str | None,
        digest: str,
    ) -> None:
        sql = lf.sql
        reason = ""
        for attempt in (1, 2):  # 首次 + 至多一次语义修复(修正稿重过全量防御)
            outcome = await self._api.tool_execute(
                trace_id=trace_id,
                tenant_key=tenant_key,
                datasource_key=datasource_key,
                user_id=user_id,
                tool_name="execute_sql",
                args={"sql": sql},
                channel="report",
            )
            if outcome.status == "executed" and outcome.result is not None:
                verdict = str(outcome.result.get("verdict") or "")
                if verdict == "PASS":
                    self._fill_ok(lf, outcome.result)
                    return
                reason = str(outcome.result.get("reason") or verdict)  # BLOCKED/ERROR:语义失败
            else:
                # 防御层直接拒绝(rejected/权限/限流)或通道故障:不可修复,不绕道
                lf.status, lf.fail_reason = (
                    "MISSING",
                    f"取数失败: {outcome.reason or outcome.status}",
                )
                return
            if attempt == 1:
                if not self._budget.spend_call() or self._budget.exhausted(self._tokens()):
                    break
                repaired = await self._llm.chat(
                    rp.REPAIR_SYSTEM_PROMPT,
                    rp.REPAIR_USER_TEMPLATE.format(
                        sql=sql,
                        reason=reason,
                        datasource_key=datasource_key,
                        schema_digest=digest,
                    ),
                    temperature=0.0,
                    trace_id=trace_id,
                    tenant_key=tenant_key,
                )
                repaired_sql = repaired.strip().rstrip(";").strip()
                if repaired_sql.upper().startswith("SELECT"):
                    sql = repaired_sql
                    continue
        lf.status, lf.fail_reason = "MISSING", f"取数失败: {reason}"

    def _fill_ok(self, lf: _Leaf, result: dict[str, object]) -> None:
        columns = [str(c) for c in cast("list[object]", result.get("columns") or [])]
        rows_raw = cast("list[object]", result.get("rows") or [])
        rows = [list(cast("list[object]", r)) for r in rows_raw]
        cap = self._settings.report_snapshot_rows
        kept = rows[:cap]
        lf.columns, lf.rows = columns, kept
        # row_count=截断前总行数(工具通道如实返回;截断后行数不是总数)
        lf.row_count = len(rows)
        lf.truncated = len(rows) > len(kept)
        lf.status = "OK"

    # ── 撰写 ──

    async def _write_chapter(
        self, ch: dict[str, Any], leaves: list[_Leaf], trace_id: str, tenant_key: str
    ) -> str:
        ok_leaves = [lf for lf in leaves if lf.status == "OK"]
        parts: list[str] = []
        snapshot_text = "\n\n".join(
            f"## {lf.title}\n{rp.render_snapshot_text(lf.columns, lf.rows)}" for lf in ok_leaves
        )
        if not snapshot_text:
            # 全章缺数:直接确定性标注,不调用 LLM
            reasons = "、".join(
                f"{lf.title}({lf.fail_reason or '数据缺失'})" for lf in leaves if lf.status != "OK"
            )
            return f"本章数据缺失:{reasons}。"
        narrative = await self._llm.chat(
            rp.WRITE_SYSTEM_PROMPT,
            rp.WRITE_USER_TEMPLATE.format(
                section_title=str(ch["title"]), snapshot_text=snapshot_text
            ),
            temperature=0.3,
            trace_id=trace_id,
            tenant_key=tenant_key,
        )
        parts.append(narrative.strip())
        missing = [lf for lf in leaves if lf.status == "MISSING"]
        if missing:
            parts.append(
                "\n\n> 数据缺失:"
                + "、".join(f"{lf.title}({lf.fail_reason or '执行失败'})" for lf in missing)
            )
        return "\n\n".join(parts)

    # ── 组装 ──

    def _assemble(
        self,
        *,
        title: str,
        chapters: list[tuple[dict[str, Any], list[_Leaf]]],
        narratives: dict[str, str],
        error: str | None,
    ) -> dict[str, object]:
        sections: list[dict[str, object]] = []
        ord_no = 0
        for ch, leaves in chapters:
            ch_id = str(ch["id"])
            ch_narrative = narratives.get(ch_id)
            if ch_narrative is None and leaves:
                # 未撰写(预算停):如实标注,不虚构
                ch_narrative = ""
            sections.append(
                {
                    "section_id": ch_id,
                    "parent_id": None,
                    "title": str(ch["title"]),
                    "narrative_md": ch_narrative or "",
                    "bindings": [],
                    "chart": None,
                    "ord": ord_no,
                    "data_status": "OK"
                    if ch_narrative and all(lf.status == "OK" for lf in leaves)
                    else ("MISSING" if any(lf.status == "MISSING" for lf in leaves) else "PENDING"),
                    "fail_reason": None if ch_narrative else (self._stop_reason or "未完成"),
                }
            )
            ord_no += 1
            for lf in leaves:
                binding: dict[str, object] = {
                    "sql": lf.sql,
                    "status": lf.status,
                    "columns": lf.columns,
                    "rows": lf.rows,
                    "row_count": lf.row_count,
                    "truncated": lf.truncated,
                    "fail_reason": lf.fail_reason,
                }
                sections.append(
                    {
                        "section_id": lf.leaf_id,
                        "parent_id": ch_id,
                        "title": lf.title,
                        "narrative_md": "",
                        "bindings": [binding],
                        "chart": lf.chart,
                        "ord": ord_no,
                        "data_status": lf.status,
                        "fail_reason": lf.fail_reason,
                    }
                )
                ord_no += 1
        return {
            "success": True,
            "title": title,
            "partial": self._partial,
            "error": error,
            "usage": self._usage_summary(),
            "sections": sections,
        }

    def _error_payload(self, message: str) -> dict[str, object]:
        return {
            "success": False,
            "title": str(self._payload.get("title") or ""),
            "partial": False,
            "error": message,
            "usage": self._usage_summary(),
            "sections": [],
        }

    # ── 工具 ──

    def _tokens(self) -> int:
        def _num(key: str) -> int:
            return sum(int(cast("int", r.get(key) or 0)) for r in self._collector)

        return _num("prompt_tokens") + _num("completion_tokens")

    def _usage_summary(self) -> dict[str, object]:
        def _num(key: str) -> int:
            return sum(int(cast("int", r.get(key) or 0)) for r in self._collector)

        return {
            "llm_calls": len(self._collector),
            "prompt_tokens": _num("prompt_tokens"),
            "completion_tokens": _num("completion_tokens"),
            "elapsed_ms": self._budget.elapsed_ms(),
        }

    def _mark_budget_stop(self, leaves: list[_Leaf]) -> None:
        self._partial = True
        self._stop_reason = "预算耗尽,部分小节未完成"
        for lf in leaves:
            if lf.status == "PENDING":
                lf.fail_reason = "预算耗尽,未取数"

    async def _progress(self, report_id: str, phase: str) -> None:
        if not report_id:
            return
        # 进度回写携带租户键，API 会校验报告归属。
        tenant_key = str(self._payload.get("tenant_key") or "default")
        await self._api.report_progress(report_id=report_id, phase=phase, tenant_key=tenant_key)


async def run_report_generation(
    *, payload: dict[str, object], llm: ReportLlmPort, api: ReportApiPort, settings: Settings
) -> dict[str, object]:
    """模块入口(端点薄壳调用;依赖显式注入便于剧本化测试)。"""
    return await ReportOrchestrator(payload=payload, llm=llm, api=api, settings=settings).run()
