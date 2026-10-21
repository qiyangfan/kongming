"""Judge 运行记录与逐对象结果的仓储。评估工具写入；基线查询仅读取 Judge 表，不写入业务表。"""

from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from kongming.db import uuid7
from kongming.evalj.models import JudgeOutcome, JudgeRunRecord, RunSummary


class SqlJudgeRepo:
    """judge_run/judge_result 读写(asyncpg/SQLAlchemy 同款连接形态)。"""

    def __init__(self, conn: AsyncConnection | AsyncSession) -> None:
        # 两者都实现 .execute(harness 侧经 get_sessionmaker 会话持有)
        self._conn = conn

    async def create_run(
        self,
        *,
        object_type: str,
        rubric_version: str,
        prompt_fingerprint: str,
        note: str = "",
    ) -> str:
        run_id = str(uuid7())
        await self._conn.execute(
            text(
                "INSERT INTO public.judge_run"
                " (id, object_type, rubric_version, prompt_fingerprint, status, note)"
                " VALUES (:id, :ot, :rv, :pf, 'RUNNING', :note)"
            ),
            {
                "id": run_id,
                "ot": object_type,
                "rv": rubric_version,
                "pf": prompt_fingerprint,
                "note": note,
            },
        )
        return run_id

    async def save_result(self, run_id: str, outcome: JudgeOutcome, object_ref: str) -> None:
        await self._conn.execute(
            text(
                "INSERT INTO public.judge_result"
                " (id, run_id, object_id, object_ref, verdict, dimensions, quotes,"
                "  raw_output, error)"
                " VALUES (:id, :run_id, :oid, :oref, CAST(:verdict AS VARCHAR),"
                "  CAST(:dims AS jsonb), CAST(:quotes AS jsonb), :raw, :err)"
            ),
            {
                "id": str(uuid7()),
                "run_id": run_id,
                "oid": outcome.object_id,
                "oref": object_ref[:200],
                "verdict": outcome.verdict,
                "dims": _json_str(outcome.dimensions),
                "quotes": _json_str(outcome.quotes),
                "raw": outcome.raw_output or None,
                "err": outcome.error or None,
            },
        )

    async def finish_run(self, run_id: str, summary: RunSummary, status: str) -> None:
        await self._conn.execute(
            text(
                "UPDATE public.judge_run SET status = CAST(:status AS VARCHAR),"
                " total_objects = :total, pass_count = :passed, borderline_count = :bl,"
                " fail_count = :failed, error_count = :errored, pass_rate = :rate,"
                " finished_at = now()"
                " WHERE id = :id"
            ),
            {
                "status": status,
                "total": summary.total,
                "passed": summary.passed,
                "bl": summary.borderline,
                "failed": summary.failed,
                "errored": summary.errored,
                "rate": summary.pass_rate,
                "id": run_id,
            },
        )

    async def latest_completed(
        self, *, object_type: str, exclude_run_id: str
    ) -> JudgeRunRecord | None:
        """查找同类对象最近一次已完成的评估运行，排除当前运行。"""
        row = (
            (
                await self._conn.execute(
                    text(
                        "SELECT id, object_type, rubric_version, prompt_fingerprint, status,"
                        " total_objects, pass_count, pass_rate, created_at"
                        " FROM public.judge_run"
                        " WHERE object_type = CAST(:ot AS VARCHAR) AND status = 'COMPLETED'"
                        " AND id <> :excl"
                        " ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"ot": object_type, "excl": exclude_run_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return JudgeRunRecord(
            id=str(row["id"]),
            object_type=str(row["object_type"]),
            rubric_version=str(row["rubric_version"]),
            prompt_fingerprint=str(row["prompt_fingerprint"]),
            status=str(row["status"]),
            total_objects=int(row["total_objects"]),
            pass_count=int(row["pass_count"]),
            pass_rate=cast("float | None", row["pass_rate"]),
            created_at=cast("object", row["created_at"]),  # type: ignore[arg-type]
        )

    async def list_results(self, run_id: str) -> list[JudgeOutcome]:
        rows = (
            (
                await self._conn.execute(
                    text(
                        "SELECT object_id, verdict, dimensions, quotes, raw_output, error"
                        " FROM public.judge_result WHERE run_id = :rid ORDER BY created_at"
                    ),
                    {"rid": run_id},
                )
            )
            .mappings()
            .all()
        )
        import json

        def _loads(value: object, fallback: object) -> object:
            # JSONB 驱动可能已解码为 dict/list(SQLAlchemy 路径),str 形态=直连驱动;双态兼容
            if isinstance(value, (dict, list)):
                return cast("object", value)
            try:
                parsed: object = json.loads(str(value)) if str(value).strip() else fallback
            except json.JSONDecodeError:
                return fallback
            return parsed

        out: list[JudgeOutcome] = []
        for r in rows:
            dims = _loads(r["dimensions"], {})
            quotes = _loads(r["quotes"], [])
            out.append(
                JudgeOutcome(
                    object_id=str(r["object_id"]),
                    verdict=str(r["verdict"]),
                    dimensions=cast("dict[str, int]", dims) if isinstance(dims, dict) else {},
                    quotes=cast("list[str]", quotes) if isinstance(quotes, list) else [],
                    raw_output=str(r["raw_output"] or ""),
                    error=str(r["error"] or ""),
                )
            )
        return out


def _json_str(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
