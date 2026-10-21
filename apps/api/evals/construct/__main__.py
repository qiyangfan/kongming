"""评估题自动构造命令。generate 生成并校验候选题；人工审核后用 merge 并入评估集并重跑完整评估。"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportPrivateUsage=false, reportUnusedImport=false

import argparse
import asyncio
import sys
from typing import Any, cast
from urllib.parse import quote

from evals.construct.generate import (
    CANDIDATES_DIR,
    generate_candidates,
    read_schema_knowledge,
    self_check,
    write_candidates,
)
from evals.judge.client import JudgeLlm


def _pg_dsn() -> str:
    """gold 直连 DSN(asyncpg 形态;KM_EVAL_PG_DSN 显式优先,缺省自控制面派生)。
    与 conftest._pg_dsn 同口径但不 import conftest(CLI 不该背评估层 skip 语义)。"""
    import os
    import urllib.parse

    from kongming.config import get_settings

    url = os.getenv("KM_EVAL_PG_DSN") or get_settings().database_url
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql://"):
        return url
    parts = urllib.parse.urlsplit(url)
    raise SystemExit(f"不识别的 DSN 形态: {parts.scheme}://…")


async def _datasource_dsn(*, tenant_id: str, datasource_key: str) -> str:
    """业务库直连 DSN(与 api 执行路由同一套解析+解密,控制面零配置)。
    datasource_key=default 即平台库路径(租户 schema 由 SQL 显式限定),无行可解析。"""
    if datasource_key == "default":
        return _pg_dsn()
    from kongming.config import get_settings
    from kongming.datasource.cipher import get_cipher
    from kongming.datasource.resolver import resolve_for_read
    from kongming.db import get_sessionmaker

    maker = get_sessionmaker()
    async with maker() as session:
        resolved, password = await resolve_for_read(
            session,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            cipher=get_cipher(get_settings().datasource_encrypt_key),
        )
    # 用户名/密码经 URL 编码:凭证含 @ : / # ? 等保留字符时裸拼会被 DSN
    # 误切分(连错主机或连接失败),与 _pg_dsn 的透传形态(已编码)对齐
    return (
        f"postgresql://{quote(str(resolved.username), safe='')}:{quote(password, safe='')}"
        f"@{resolved.host}:{resolved.port}/{resolved.database}"
    )


async def _cmd_generate(args: argparse.Namespace) -> int:
    import asyncpg

    tenant_id = await _resolve_tenant_id(args.tenant)
    # 双连接:知识面读控制面库(rag.table_descriptions);gold 自洽验证直连
    # 业务库(与 eval 域 gold 执行同一落点,数据源解析器同一套解密)
    knowledge_conn = cast("Any", await asyncpg.connect(_pg_dsn()))
    try:
        knowledge = await read_schema_knowledge(knowledge_conn, tenant_id, args.datasource)
    finally:
        await knowledge_conn.close()
    if not knowledge.strip():
        print(
            f"租户 {args.tenant}/{args.datasource} 无表知识(rag.table_descriptions 空)"
            "——先跑 /system/rag/fetch-metadata + /reindex",
            file=sys.stderr,
        )
        return 2
    llm = JudgeLlm()
    gen_report: dict[str, int] = {}
    candidates = await generate_candidates(
        llm, knowledge, per_category=args.per_category, report=gen_report
    )
    schema_name = next((c.schema_name for c in candidates if c.schema_name), args.schema)
    datasource_conn = cast(
        "Any",
        await asyncpg.connect(
            await _datasource_dsn(tenant_id=tenant_id, datasource_key=args.datasource)
        ),
    )
    try:
        await self_check(datasource_conn, llm, candidates, schema_name=schema_name)
    finally:
        await datasource_conn.close()
    passed = sum(1 for c in candidates if c.passed)
    path = write_candidates(args.tenant, candidates)
    print(f"候选 {len(candidates)} 条(自洽验证通过 {passed})→ {path}")
    if gen_report.get("failed_batches"):
        print(
            f"警告:{gen_report['failed_batches']} 个类别的生成批次失败(LLM 调用/解析),"
            "候选可能按类别缺题——必要时重跑 generate 补批",
            file=sys.stderr,
        )
    print("人审:编辑候选文件圈定 id 后执行 merge 子命令并入(唯一并入路径)")
    return 0


async def _resolve_tenant_id(tenant_key: str) -> str:

    import asyncpg

    conn = await asyncpg.connect(_pg_dsn())
    try:
        row = await conn.fetchrow("SELECT id FROM sys_tenant WHERE tenant_key = $1", tenant_key)
    finally:
        await conn.close()
    if row is None:
        raise SystemExit(f"租户不存在: {tenant_key}")
    return str(cast("object", row["id"]))


def _cmd_merge(args: argparse.Namespace) -> int:
    from evals.construct.merge import merge

    # 候选目录固定单批形态:只认最新一批;多批靠人工滚动归档
    tenant_dir = CANDIDATES_DIR / args.tenant
    batches = sorted(tenant_dir.glob("*.yaml"), key=lambda p: p.stat().st_mtime)
    if not batches:
        print(f"无候选批次: {tenant_dir}", file=sys.stderr)
        return 2
    latest = batches[-1]
    target = tenant_dir / "candidates.yaml"
    if target.exists() and target != latest:
        # POSIX rename 对已存在目标是静默替换——上一批未并入的人审圈定/编辑
        # 内容会被无提示覆盖。人审产物必须显式让位(归档或删除),不静默吞。
        print(
            f"已存在 {target}(上一批人审产物)。先归档/删除它再 merge,"
            f"或将本批 {latest.name} 手工合入后重试",
            file=sys.stderr,
        )
        return 2
    latest.rename(target)
    merge(
        args.tenant,
        [i.strip() for i in args.ids.split(",") if i.strip()],
        with_full_eval=not args.no_full_eval,
        reason=args.reason,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals.construct")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="生成候选+自洽验证落目录")
    p_gen.add_argument("--tenant", required=True)
    p_gen.add_argument("--datasource", required=True)
    p_gen.add_argument("--per-category", type=int, default=2)
    p_gen.add_argument("--schema", default="public")
    p_gen.set_defaults(func=_cmd_generate)

    p_merge = sub.add_parser("merge", help="人审圈定后并入+全量重跑+基线登记")
    p_merge.add_argument("--tenant", required=True)
    p_merge.add_argument("--ids", required=True, help="逗号分隔的候选 id")
    p_merge.add_argument("--no-full-eval", action="store_true", help="只并入不重跑全量")
    p_merge.add_argument("--reason", default="自动构造扩题(092 三段闸人审并入)")
    p_merge.set_defaults(func=_cmd_merge)

    args = parser.parse_args()
    result = args.func(args)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    raise SystemExit(result if isinstance(result, int) else 0)


if __name__ == "__main__":
    main()
