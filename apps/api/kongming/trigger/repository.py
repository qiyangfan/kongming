"""trigger 存储:sys_trigger_definition/sys_trigger_run。"""

import datetime as dt
import json
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.uuid7 import uuid7


def _ts(v: Any) -> dt.datetime | None:
    """ISO 串/None → datetime(asyncpg 需 Python 侧类型,SQL CAST 无法兜底)。"""
    if v is None or isinstance(v, dt.datetime):
        return v
    return dt.datetime.fromisoformat(str(v))


class SqlTriggerRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, **kw: Any) -> str:
        rid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_trigger_definition"
                " (id, tenant_id, tg_key, name, type, target_type, target_key,"
                "  cron_expr, min_interval_sec, webhook_path, secret_cipher, adapter,"
                "  notify_url, created_by, next_fire_at, max_retries,"
                "  imap_host, imap_port, imap_tls, imap_user, imap_credential,"
                "  imap_folder, imap_poll_interval_sec, task_template)"
                " VALUES (:id, CAST(:t AS uuid), :k, :n, :ty, :tt, :tk, :cron,"
                "         :mis, CAST(:wp AS uuid), :sec, :ad, :nu,"
                "         CAST(:cb AS uuid), CAST(:nf AS timestamptz), :mr,"
                "         :ih, :ip, :itl, :iu, :ic, :ifo, :ipi, CAST(:tpl AS jsonb))"
            ),
            {
                "id": rid,
                "t": kw["tenant_id"],
                "k": kw["tg_key"],
                "n": kw["name"],
                "ty": kw["type"],
                "tt": kw["target_type"],
                "tk": kw["target_key"],
                "cron": kw.get("cron_expr"),
                "mis": kw.get("min_interval_sec", 60),
                "wp": kw.get("webhook_path"),
                "sec": kw.get("secret_cipher", ""),
                "ad": kw.get("adapter", "custom"),
                "nu": kw.get("notify_url", ""),
                "cb": kw["created_by"],
                "nf": _ts(kw.get("next_fire_at")),
                "mr": kw.get("max_retries", 0),
                "ih": kw.get("imap_host", ""),
                "ip": kw.get("imap_port", 993),
                "itl": bool(kw.get("imap_tls", True)),
                "iu": kw.get("imap_user", ""),
                "ic": kw.get("imap_credential", ""),
                "ifo": kw.get("imap_folder", "INBOX"),
                "ipi": kw.get("imap_poll_interval_sec", 300),
                "tpl": (
                    json.dumps(kw["task_template"], ensure_ascii=False)
                    if kw.get("task_template") is not None
                    else None
                ),
            },
        )
        return rid

    async def update(self, **kw: Any) -> bool:
        # 邮件配置列仅 EMAIL 行更新(service 只在 EMAIL 形态传入);
        # 凭证空框=保持现值(与 secret_cipher 同 COALESCE 口径)。
        # 两条完整字面量语句(非片段拼接):形态分支显式可读,且不触发 S608
        with_imap = "imap_host" in kw
        params: dict[str, Any] = {
            "n": kw["name"],
            "tt": kw["target_type"],
            "tk": kw["target_key"],
            "cron": kw.get("cron_expr"),
            "mis": kw.get("min_interval_sec", 60),
            "nf": _ts(kw.get("next_fire_at")),
            "ad": kw.get("adapter", "custom"),
            "nu": kw.get("notify_url", ""),
            "sec": kw.get("secret_cipher", ""),
            "mr": kw.get("max_retries", 0),
            "i": kw["trigger_id"],
            "t": kw["tenant_id"],
        }
        base_set = (
            "UPDATE public.sys_trigger_definition SET name=:n,"
            " target_type=:tt, target_key=:tk, cron_expr=:cron,"
            " min_interval_sec=:mis, adapter=:ad, notify_url=:nu,"
            " secret_cipher=COALESCE(NULLIF(:sec, ''), secret_cipher),"
            " next_fire_at=CAST(:nf AS timestamptz),"
            " max_retries=:mr, task_template=CAST(:tpl AS jsonb), updated_at=now()"
        )
        where = " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
        params["tpl"] = (
            json.dumps(kw["task_template"], ensure_ascii=False)
            if kw.get("task_template") is not None
            else None
        )
        if with_imap:
            sql = (
                base_set + ", imap_host=:ih, imap_port=:ip, imap_tls=:itl, imap_user=:iu,"
                " imap_folder=:ifo, imap_poll_interval_sec=:ipi,"
                " imap_credential=COALESCE(NULLIF(:ic, ''), imap_credential)" + where
            )
            params.update(
                {
                    "ih": kw.get("imap_host", ""),
                    "ip": kw.get("imap_port", 993),
                    "itl": bool(kw.get("imap_tls", True)),
                    "iu": kw.get("imap_user", ""),
                    "ifo": kw.get("imap_folder", "INBOX"),
                    "ipi": kw.get("imap_poll_interval_sec", 300),
                    "ic": kw.get("imap_credential", ""),
                }
            )
        else:
            sql = base_set + where
        res = cast(
            "CursorResult[object]",
            await self._s.execute(text(sql), params),
        )
        return res.rowcount > 0

    async def get(self, *, tenant_id: str, tg_key: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_trigger_definition"
                        " WHERE tenant_id=CAST(:t AS uuid) AND tg_key=:k"
                    ),
                    {"t": tenant_id, "k": tg_key},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_by_id(self, *, trigger_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text("SELECT * FROM public.sys_trigger_definition WHERE id=CAST(:i AS uuid)"),
                    {"i": trigger_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def list_all(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_trigger_definition"
                        " WHERE tenant_id=CAST(:t AS uuid) ORDER BY created_at DESC, id DESC"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def set_enabled(self, *, trigger_id: str, tenant_id: str, enabled: bool) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_trigger_definition SET enabled=:e, updated_at=now()"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"e": enabled, "i": trigger_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def delete(self, *, trigger_id: str, tenant_id: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "DELETE FROM public.sys_trigger_definition"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"i": trigger_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    async def find_by_webhook_path(self, *, path: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_trigger_definition"
                        " WHERE webhook_path=CAST(:p AS uuid)"
                    ),
                    {"p": path},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def list_due_cron(self, *, now_iso: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_trigger_definition"
                        " WHERE type='CRON' AND enabled AND next_fire_at IS NOT NULL"
                        " AND next_fire_at <= CAST(:now AS timestamptz)"
                    ),
                    {"now": _ts(now_iso)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def list_due_emails(self, *, now_iso: str) -> list[dict[str, Any]]:
        """到期邮件触发器:轮询间隔即 next_fire_at 节流,独立于 cron
        表达式;停用/删除即出扫描面(启用后从游标续传)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_trigger_definition"
                        " WHERE type='EMAIL' AND enabled AND next_fire_at IS NOT NULL"
                        " AND next_fire_at <= CAST(:now AS timestamptz)"
                    ),
                    {"now": _ts(now_iso)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_email_cursor(self, *, trigger_id: str, folder: str) -> dict[str, Any] | None:
        """UID 游标:每触发器×文件夹一行;无行=首次轮询(从 0 起步)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_trigger_email_cursor"
                        " WHERE trigger_id=CAST(:g AS uuid) AND folder=:f"
                    ),
                    {"g": trigger_id, "f": folder},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def upsert_email_cursor(
        self, *, trigger_id: str, folder: str, last_uid: int, uidvalidity: int
    ) -> None:
        """游标推进:与触发记录同会话,由调用方 commit 同事务落定。"""
        await self._s.execute(
            text(
                "INSERT INTO public.sys_trigger_email_cursor"
                " (id, trigger_id, folder, last_uid, uidvalidity)"
                " VALUES (CAST(:i AS uuid), CAST(:g AS uuid), :f, :u, :uv)"
                " ON CONFLICT (trigger_id, folder)"
                " DO UPDATE SET last_uid=:u, uidvalidity=:uv, updated_at=now()"
            ),
            {
                "i": str(uuid7()),
                "g": trigger_id,
                "f": folder,
                "u": last_uid,
                "uv": uidvalidity,
            },
        )

    async def mark_fired(
        self, *, trigger_id: str, last_fired_iso: str, next_fire_iso: str | None
    ) -> None:
        await self._s.execute(
            text(
                "UPDATE public.sys_trigger_definition"
                " SET last_fired_at=CAST(:lf AS timestamptz),"
                " next_fire_at=CAST(:nf AS timestamptz), updated_at=now()"
                " WHERE id=CAST(:i AS uuid)"
            ),
            {"lf": _ts(last_fired_iso), "nf": _ts(next_fire_iso), "i": trigger_id},
        )

    async def insert_run(
        self,
        *,
        tenant_id: str,
        trigger_id: str,
        status: str,
        error: str,
        duration_ms: int,
        target_run_ref: str,
        attempt: int = 1,
        retry_of_run_id: str | None = None,
        next_retry_at: Any | None = None,
        summary: str = "",
    ) -> str:
        rid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_trigger_run"
                " (id, tenant_id, trigger_id, status, error, duration_ms, target_run_ref,"
                "  retry_of_run_id, attempt, next_retry_at, summary)"
                " VALUES (:i, CAST(:t AS uuid), CAST(:g AS uuid), :st, :e, :d, :ref,"
                "         CAST(:rof AS uuid), :at, CAST(:nra AS timestamptz), :sum)"
            ),
            {
                "i": rid,
                "t": tenant_id,
                "g": trigger_id,
                "st": status,
                "e": error[:2000],
                "d": duration_ms,
                "ref": target_run_ref,
                "rof": retry_of_run_id,
                "at": attempt,
                "nra": _ts(next_retry_at),
                "sum": summary,
            },
        )
        return rid

    async def latest_success_run_for_thread(
        self, *, trigger_id: str, thread_key: str
    ) -> dict[str, Any] | None:
        """同线程键的最新成功 run(线程去重判定;summary 为 JSON 串,
        非对象形态(旧行/空)经 CASE 守卫不参与匹配,不炸 cast)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, summary, target_run_ref, created_at"
                        " FROM public.sys_trigger_run"
                        " WHERE trigger_id=CAST(:t AS uuid) AND status='SUCCESS'"
                        "   AND CASE WHEN summary LIKE '{%'"
                        "       THEN summary::jsonb->>'thread_key' END = :tk"
                        " ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"t": trigger_id, "tk": thread_key},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_run(self, *, run_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text("SELECT * FROM public.sys_trigger_run WHERE id=CAST(:i AS uuid)"),
                    {"i": run_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def list_due_retries(self, *, now_iso: str) -> list[dict[str, Any]]:
        """到期挂起重试:PENDING+next_retry_at 到期;部分索引覆盖。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_trigger_run"
                        " WHERE status='PENDING' AND next_retry_at IS NOT NULL"
                        " AND next_retry_at <= CAST(:now AS timestamptz)"
                        " ORDER BY next_retry_at, id"
                    ),
                    {"now": _ts(now_iso)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def update_run_terminal(
        self,
        *,
        run_id: str,
        status: str,
        error: str,
        duration_ms: int,
        target_run_ref: str,
    ) -> int:
        """挂起重试行原行落定终态(不覆盖先前失败行;AND PENDING 防双派发)。
        返回命中行数:0=行已被外部取消(停用/删除竞态),调用方不再续链。"""
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_trigger_run"
                    " SET status=:st, error=:e, duration_ms=:d, target_run_ref=:ref,"
                    "     next_retry_at=NULL"
                    " WHERE id=CAST(:i AS uuid) AND status='PENDING'"
                ),
                {
                    "st": status,
                    "e": error[:2000],
                    "d": duration_ms,
                    "ref": target_run_ref,
                    "i": run_id,
                },
            ),
        )
        return int(res.rowcount or 0)

    async def defer_retry(self, *, run_id: str, next_retry_at: Any) -> None:
        """挂起重试顺延(执行中永不并发:撞上在途执行推迟一个退避间隔)。"""
        await self._s.execute(
            text(
                "UPDATE public.sys_trigger_run SET next_retry_at=CAST(:nr AS timestamptz)"
                " WHERE id=CAST(:i AS uuid) AND status='PENDING'"
            ),
            {"nr": _ts(next_retry_at), "i": run_id},
        )

    async def cancel_pending_retries(self, *, trigger_id: str, reason: str) -> None:
        """停用/删除取消挂起重试:收敛为 FAILED 终态,历史可见。"""
        await self._s.execute(
            text(
                "UPDATE public.sys_trigger_run"
                " SET status='FAILED', error=:r, next_retry_at=NULL"
                " WHERE trigger_id=CAST(:g AS uuid) AND status='PENDING'"
            ),
            {"r": reason[:2000], "g": trigger_id},
        )

    async def page_runs(
        self, *, tenant_id: str, trigger_id: str, page: int, size: int
    ) -> tuple[int, list[dict[str, Any]]]:
        total = int(
            (
                await self._s.execute(
                    text(
                        "SELECT count(*) FROM public.sys_trigger_run"
                        " WHERE tenant_id=CAST(:t AS uuid) AND trigger_id=CAST(:g AS uuid)"
                    ),
                    {"t": tenant_id, "g": trigger_id},
                )
            ).scalar_one()
        )
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_trigger_run"
                        " WHERE tenant_id=CAST(:t AS uuid) AND trigger_id=CAST(:g AS uuid)"
                        " ORDER BY created_at DESC, id DESC LIMIT :lim OFFSET :off"
                    ),
                    {"t": tenant_id, "g": trigger_id, "lim": size, "off": (page - 1) * size},
                )
            )
            .mappings()
            .all()
        )
        return total, [dict(r) for r in rows]

    async def commit(self) -> None:
        await self._s.commit()
