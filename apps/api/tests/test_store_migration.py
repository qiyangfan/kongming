"""104 迁移契约测试:0047 升级/降级往返 + 存量在架 Bot v1 回填对拍。

存量零回归铁律的对拍形态:在 0046(旧 schema)插入在架 Bot → 采集
商店可见字段基线 → 升级到 head → 断言 v1 快照=基线(逐字段)且新代码
商店查询与基线一致;再验证 downgrade 往返后契约复现。
注意:每条 alembic 命令前 session.rollback() 清场(连接持 ACCESS SHARE
会死等 ALTER 的 ACCESS EXCLUSIVE,103 期实证)。"""
# pyright: reportPrivateUsage=false

import asyncio
import json
from typing import cast

from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from tests.conftest import API_ROOT, MakeUser
from tests.test_chat_query import _bind
from tests.test_store import _user  # 归属用户 arrange 单源


def _cfg() -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


async def _run_alembic(session: AsyncSession, target: str) -> None:
    """执行一条 alembic 命令(前 rollback 清场);head=升级,其余=降级目标。"""
    await session.rollback()
    if target == "head":
        await asyncio.to_thread(command.upgrade, _cfg(), "head")
    else:
        await asyncio.to_thread(command.downgrade, _cfg(), target)


async def _mk_published_row(session: AsyncSession) -> dict[str, str]:
    """旧 schema(0046)直插在架 Bot+租户(不经应用代码,存量形态造数)。"""
    tid = str(
        (
            await session.execute(
                text(
                    "INSERT INTO public.sys_tenant (id, tenant_key, tenant_name)"
                    " VALUES (gen_random_uuid(), 'mig_t', '迁移租户') RETURNING id"
                )
            )
        ).scalar_one()
    )
    uid = str(
        (
            await session.execute(
                text(
                    "INSERT INTO public.sys_user (id, username, status, password_changed_at)"
                    " VALUES (gen_random_uuid(), 'mig_u', 'ACTIVE', now()) RETURNING id"
                )
            )
        ).scalar_one()
    )
    bot_id = str(
        (
            await session.execute(
                text(
                    "INSERT INTO public.sys_bot_definition"
                    " (id, tenant_id, bot_key, name, system_prompt, tools, greeting,"
                    "  suggestions, published_at, published_by, created_by)"
                    " VALUES (gen_random_uuid(), CAST(:t AS uuid), 'mig_bot', '迁移助手',"
                    "  '你是迁移助手', CAST(:tools AS jsonb), '迁移问候',"
                    "  CAST(:sug AS jsonb), now(), CAST(:u AS uuid), CAST(:u AS uuid))"
                    " RETURNING id"
                ),
                {
                    "t": tid,
                    "tools": json.dumps(["execute_sql"]),
                    "sug": json.dumps(["问迁移"]),
                    "u": uid,
                },
            )
        ).scalar_one()
    )
    return {"tenant_id": tid, "bot_id": bot_id}


async def test_migration_0047_backfill_and_store_parity(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser
) -> None:
    """存量在架零回归:v1 快照=迁移前行字段;商店查询与迁移前基线逐字段一致。"""
    from kongming.store.repository import SqlStoreRepo

    await _run_alembic(session, "0046")
    made = await _mk_published_row(session)
    await session.commit()
    # 迁移前基线:商店可见字段直采(0046 schema 上与旧商店查询同源同形)
    base = (
        (
            await session.execute(
                text(
                    "SELECT bot_key, name, greeting, suggestions, system_prompt, tools"
                    " FROM public.sys_bot_definition WHERE id=CAST(:i AS uuid)"
                ),
                {"i": made["bot_id"]},
            )
        )
        .mappings()
        .one()
    )
    # 升级回 head:存量在架补 v1 快照并指向
    await _run_alembic(session, "head")
    ver = (
        (
            await session.execute(
                text(
                    "SELECT v.version_no, v.snapshot, b.published_at,"
                    " (b.current_version_id IS NOT NULL) AS pointed"
                    " FROM public.sys_bot_definition b"
                    " JOIN public.sys_bot_version v ON v.id=b.current_version_id"
                    " WHERE b.id=CAST(:i AS uuid)"
                ),
                {"i": made["bot_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert ver["version_no"] == 1 and ver["pointed"] and ver["published_at"] is not None
    snap_raw: object = ver["snapshot"]
    assert isinstance(snap_raw, dict)
    snap = cast("dict[str, object]", snap_raw)
    assert snap["name"] == base["name"]
    assert snap["greeting"] == base["greeting"]
    assert snap["suggestions"] == base["suggestions"]
    assert snap["system_prompt"] == base["system_prompt"]
    assert snap["tools"] == base["tools"]
    # 未上架存量不补:插一条未上架 Bot,断言无版本行
    draft_id = str(
        (
            await session.execute(
                text(
                    "INSERT INTO public.sys_bot_definition"
                    " (id, tenant_id, bot_key, name, system_prompt, tools, created_by)"
                    " SELECT gen_random_uuid(), tenant_id, 'mig_draft', '草稿助手', 'p',"
                    "  CAST('[]' AS jsonb), created_by"
                    " FROM public.sys_bot_definition WHERE id=CAST(:i AS uuid)"
                    " RETURNING id"
                ),
                {"i": made["bot_id"]},
            )
        ).scalar_one()
    )
    await session.commit()
    n = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_bot_version WHERE bot_id=CAST(:i AS uuid)"),
            {"i": draft_id},
        )
    ).scalar_one()
    assert n == 0
    # 商店查询对拍:新代码 store_list/store_get 消费快照,与基线逐字段一致
    got = await SqlStoreRepo(session).store_get(
        tenant_id=made["tenant_id"], bot_key=str(base["bot_key"])
    )
    assert got is not None
    assert got["name"] == base["name"]
    assert got["greeting"] == base["greeting"]
    assert got["suggestions"] == base["suggestions"]
    assert got["version_no"] == 1
    listing = await SqlStoreRepo(session).store_list(
        tenant_id=made["tenant_id"], keyword="", sort="new"
    )
    hit = [r for r in listing if str(r["id"]) == made["bot_id"]]
    assert len(hit) == 1
    assert hit[0]["name"] == base["name"] and hit[0]["greeting"] == base["greeting"]
    # 端到端:商店详情(HTTP 面)同样消费 v1 快照,行为与迁移前一致
    u, token = await _user(client, session, make_user, "mig_admin")
    await _bind(session, "sys_user_tenant", u, made["tenant_id"])
    r = await client.get(
        "/store/bots/mig_bot",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "mig_t"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == base["name"]
    assert r.json()["greeting"] == base["greeting"]
    assert r.json()["version_no"] == 1


async def test_migration_0047_downgrade_roundtrip(session: AsyncSession) -> None:
    """降级往返:down 到 0046 表列消失,再回 head 契约复现。"""
    await _run_alembic(session, "0046")
    has = (
        await session.execute(
            text(
                "SELECT count(*) FROM information_schema.tables"
                " WHERE table_schema='public' AND table_name='sys_bot_version'"
            )
        )
    ).scalar_one()
    col = (
        await session.execute(
            text(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name='sys_bot_definition' AND column_name='current_version_id'"
            )
        )
    ).scalar_one()
    assert has == 0 and col == 0
    # 必须回到 head:停旧版会让其后用例 TRUNCATE 撞不存在的表(0034 先例)
    await _run_alembic(session, "head")
    has2 = (
        await session.execute(
            text(
                "SELECT count(*) FROM information_schema.tables"
                " WHERE table_schema='public' AND table_name='sys_bot_version'"
            )
        )
    ).scalar_one()
    assert has2 == 1
