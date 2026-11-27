"""孔明运行时 break-glass CLI(独立凭证/原因/二次确认/限时/审计)。

不嵌入管理台、不新增 admin 端点;只在服务或控制面不可用时按文档化场景
直连运行时库修复。默认 dry-run,只有 --apply 且确认短语匹配才写入。

环境变量:
  KM_BREAK_GLASS_DATABASE_URL  独立库凭证(与应用运行凭证分离)
  KM_BREAK_GLASS_TOKEN         通道口令(经 secret store 注入,须与 --token 一致)
  KM_BREAK_GLASS_ACTOR         操作者标识(进审计)
  KM_BREAK_GLASS_EXPIRES_AT    时间窗截止(ISO8601,带时区)
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    select_sql: str
    update_sql: str
    max_rows: int = 1


SCENARIOS: dict[str, Scenario] = {
    "mark-task-interrupted": Scenario(
        name="mark-task-interrupted",
        description="把卡在 RUNNING/PLANNING 的任务收敛为 INTERRUPTED(不谎称完成)",
        select_sql=(
            "SELECT count(*) FROM public.sys_agent_task"
            " WHERE id = CAST(:id AS uuid) AND status IN ('RUNNING','PLANNING')"
        ),
        update_sql=(
            "UPDATE public.sys_agent_task SET status='INTERRUPTED', fail_reason=:reason,"
            " updated_at=now()"
            " WHERE id = CAST(:id AS uuid) AND status IN ('RUNNING','PLANNING')"
        ),
    ),
    "reset-report-failed": Scenario(
        name="reset-report-failed",
        description="把滞留 GENERATING 的报告标记 FAILED(避免永久悬挂)",
        select_sql=(
            "SELECT count(*) FROM public.report"
            " WHERE id = CAST(:id AS uuid) AND status = 'GENERATING'"
        ),
        update_sql=(
            "UPDATE public.report SET status='FAILED', fail_reason=:reason, updated_at=now()"
            " WHERE id = CAST(:id AS uuid) AND status = 'GENERATING'"
        ),
    ),
}


class BreakGlassRefused(Exception):
    """前置条件不满足:不连接库,直接退出。"""


def _require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise BreakGlassRefused(f"缺少 {name}")
    return value


def _check_window(now: datetime | None = None) -> None:
    raw = _require_env("KM_BREAK_GLASS_EXPIRES_AT")
    try:
        expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise BreakGlassRefused("KM_BREAK_GLASS_EXPIRES_AT 非 ISO8601") from e
    if expires.tzinfo is None:
        raise BreakGlassRefused("KM_BREAK_GLASS_EXPIRES_AT 必须带时区")
    current = now or datetime.now(UTC)
    if current > expires:
        raise BreakGlassRefused("break-glass 时间窗已过期")


def _check_token(token: str | None) -> None:
    expected = _require_env("KM_BREAK_GLASS_TOKEN")
    import secrets as _secrets

    if not token or not _secrets.compare_digest(token.encode(), expected.encode()):
        raise BreakGlassRefused("break-glass 口令不匹配")


def _validate_target(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as e:
        raise BreakGlassRefused("目标 id 必须为 UUID") from e


async def _run(scenario: Scenario, target: str, reason: str, apply: bool) -> int:
    url = _require_env("KM_BREAK_GLASS_DATABASE_URL")
    actor = _require_env("KM_BREAK_GLASS_ACTOR")
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            count = int(
                (await conn.execute(text(scenario.select_sql), {"id": target})).scalar_one()
            )
            if count > scenario.max_rows:
                raise BreakGlassRefused(f"影响行数 {count} 超过上限 {scenario.max_rows}")
            print(f"scenario={scenario.name} target={target} candidates={count}")
            if not apply or count == 0:
                return count
            result = await conn.execute(text(scenario.update_sql), {"id": target, "reason": reason})
            rows = int(cast("Any", result).rowcount or 0)
            if rows > scenario.max_rows:
                raise BreakGlassRefused(f"实际影响行数 {rows} 超过上限")
            digest = hashlib.sha256(f"{scenario.name}:{scenario.update_sql}".encode()).hexdigest()[
                :16
            ]
            await conn.execute(
                text(
                    "INSERT INTO public.sys_oper_log (id, user_id, action, target, detail)"
                    " VALUES (gen_random_uuid(), NULL, :a, :t, CAST(:d AS JSONB))"
                ),
                {
                    "a": f"break_glass.{scenario.name}",
                    "t": target,
                    "d": json.dumps(
                        {
                            "actor": actor,
                            "reason": reason,
                            "sql_digest": digest,
                            "rows": rows,
                        },
                        ensure_ascii=False,
                    ),
                },
            )
            print(f"applied rows={rows} actor={actor} sql_digest={digest}")
            return rows
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="孔明运行时 break-glass(受控修复)")
    p.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    p.add_argument("--id", required=True, dest="target")
    p.add_argument("--reason", required=True)
    p.add_argument("--token", default=None)
    p.add_argument("--confirm", default="")
    p.add_argument("--apply", action="store_true")
    return p


async def _main_async(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    reason = str(args.reason).strip()
    if len(reason) < 8:
        print("拒绝: --reason 至少 8 个字符", file=sys.stderr)
        return 2
    expected = f"APPLY {args.scenario} {args.target}"
    if args.apply and str(args.confirm).strip() != expected:
        print(f'拒绝: --apply 需要 --confirm "{expected}"', file=sys.stderr)
        return 2
    try:
        _check_token(args.token)
        _check_window()
        target = _validate_target(str(args.target))
        await _run(SCENARIOS[str(args.scenario)], target, reason, bool(args.apply))
    except BreakGlassRefused as e:
        print(f"拒绝: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 入口;返回进程退出码(0=成功,dry-run 未写入也返回 0)。"""
    return asyncio.run(_main_async(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
