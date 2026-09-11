"""知识覆盖报告全栈测试(052,安全规则类测试先行)。

纯函数(差集/归并/封顶)确定性单测 + 端点全栈(真实 PG/Redis;
AgentRagClient 类级替身带调用计数——兼证零 LLM 调用)。
安全面:manage 权限 403 / datasource 同形 400 / 租户隔离 /
内省失败不产出半份报告 / 只读零副作用(两次调用逐字节一致)。
"""
# pyright: reportPrivateUsage=false

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.rag.agent_callback import AgentRagClient
from kongming.rag.models import (
    COVERAGE_FAILURE_CLUSTER_CAP,
    COVERAGE_LIST_CAP,
    FailureSignal,
    HistoryOverview,
    IntrospectedTable,
    KnowledgeEntry,
    build_coverage_report,
    cluster_failure_signals,
    diff_unregistered_tables,
    find_missing_column_labels,
    find_no_sample_tables,
    find_structure_only_entries,
    normalize_question,
)
from tests.conftest import MakeUser
from tests.test_chat_query import _auth, _default_tenant_id
from tests.test_rag_admin import _admin_token, _noperm_user

RAG = "/system/rag"

# ============ 纯函数(无 DB;差集/归并/封顶) ============


def _it(schema: str, table: str, cols: dict[str, str] | None = None) -> IntrospectedTable:
    return IntrospectedTable(
        schema_name=schema, table_name=table, table_comment="", columns_comment=cols or {}
    )


def _entry(
    schema: str,
    table: str,
    *,
    source: str = "AI_GENERATED",
    columns_comment: dict[str, str] | None = None,
    sample_queries: list[str] | None = None,
) -> KnowledgeEntry:
    return KnowledgeEntry(
        id="x",
        tenant_id="t",
        datasource_key="default",
        schema_name=schema,
        table_name=table,
        table_comment="c",
        columns_comment=columns_comment or {},
        sample_queries=sample_queries or [],
        source_type=source,
        updated_at=datetime(2026, 1, 1),
        alias="",
    )


def test_diff_unregistered_and_cross_schema_no_shadow() -> None:
    tables = [
        _it("tenant_default", "goods"),
        _it("public", "goods"),
        _it("tenant_default", "stock"),
    ]
    registered = {("tenant_default", "goods")}
    got = diff_unregistered_tables(tables, registered)
    assert got == [("public", "goods"), ("tenant_default", "stock")]


def test_structure_only_entries() -> None:
    entries = [
        _entry("s", "a", source="INTROSPECTION"),
        _entry("s", "b", source="AI_GENERATED"),
        _entry("s", "c", source="MANUAL_EDIT"),
    ]
    assert find_structure_only_entries(entries) == [("s", "a")]


def test_missing_column_labels() -> None:
    tables = [_it("s", "t", {"id": "int", "name": "text", "note": "text"})]
    entries = [_entry("s", "t", columns_comment={"id": "int 主键"})]
    got = find_missing_column_labels(tables, entries)
    assert len(got) == 1
    assert (got[0].schema_name, got[0].table_name) == ("s", "t")
    assert sorted(got[0].missing) == ["name", "note"]
    assert got[0].missing_count == 2


def test_no_sample_tables() -> None:
    entries = [
        _entry("s", "a"),
        _entry("s", "b", sample_queries=["SELECT 1"]),
        _entry("s", "c", source="INTROSPECTION"),
    ]
    assert find_no_sample_tables(entries) == [("s", "a"), ("s", "c")]


def test_normalize_question() -> None:
    assert normalize_question("  每个 门店 的 库存 ") == normalize_question("每个门店的库存")
    assert normalize_question("Goods List") == normalize_question("goods list")


def test_cluster_merge_and_last_status() -> None:
    base = datetime(2026, 9, 1, 12, 0, 0)
    signals = [
        FailureSignal(
            question="每个门店的库存数量", status="FAILED", rating="",
            has_corrected_sql=False, created_at=base,
        ),
        FailureSignal(
            question="每个门店的库存数量 ", status="NO_DATA", rating="",
            has_corrected_sql=False, created_at=base + timedelta(hours=1),
        ),
        FailureSignal(
            question="各分类销售额", status="SUCCESS", rating="DOWN",
            has_corrected_sql=False, created_at=base,
        ),
    ]
    clusters, total = cluster_failure_signals(signals)
    assert total == 2
    merged = next(c for c in clusters if c.question.strip() == "每个门店的库存数量")
    assert merged.count == 2
    assert merged.last_status == "NO_DATA"  # 最近一次(时间更晚)的状态
    rated = next(c for c in clusters if "各分类" in c.question)
    assert rated.count == 1 and rated.last_status == "SUCCESS"


def test_cluster_tiebreak_on_desc_input() -> None:
    """仓储真实输入序(created_at DESC, id DESC)下并列 created_at:
    首行(id 更大=最新插入)保持为簇代表——严格大于才覆盖,靠后旧行不得反转。"""
    same = datetime(2026, 9, 1, 12, 0, 0)
    signals = [
        # 首行=最新插入(id 更大;同事务 now() 并列的真实形态)
        FailureSignal(
            question="每个门店的库存数量", status="NO_DATA", rating="",
            has_corrected_sql=False, created_at=same,
        ),
        FailureSignal(
            question="每个门店的库存数量", status="FAILED", rating="",
            has_corrected_sql=False, created_at=same,
        ),
    ]
    clusters, total = cluster_failure_signals(signals)
    assert total == 1
    assert clusters[0].last_status == "NO_DATA"  # 旧行(FAILED)不得覆盖
    assert clusters[0].count == 2


def test_cluster_cap_and_total() -> None:
    base = datetime(2026, 9, 1)
    signals = [
        FailureSignal(
            question=f"问题 {i}", status="FAILED", rating="",
            has_corrected_sql=False, created_at=base + timedelta(minutes=i),
        )
        for i in range(COVERAGE_FAILURE_CLUSTER_CAP + 5)
    ]
    clusters, total = cluster_failure_signals(signals)
    assert len(clusters) == COVERAGE_FAILURE_CLUSTER_CAP
    assert total == COVERAGE_FAILURE_CLUSTER_CAP + 5


def test_unregistered_cap_and_total() -> None:
    """封顶截条目、总数计数如实(build_coverage_report 兜底,spec 场景直测)。"""
    tables = [_it("s", f"t{i:03d}") for i in range(COVERAGE_LIST_CAP + 100)]
    report = build_coverage_report(
        datasource_key="d",
        tables=tables,
        entries=[],
        signals=[],
        history=HistoryOverview(success_count=0, distinct_questions=0, cap=500),
    )
    assert len(report.unregistered_tables) == COVERAGE_LIST_CAP
    assert report.unregistered_total == COVERAGE_LIST_CAP + 100


# ============ 端点全栈 ============


@pytest.fixture
def counting_agent(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """AgentRagClient 类级替身(带调用计数):兼证覆盖报告零 LLM 调用。"""
    state = {"embed": 0, "describe": 0}

    async def fake_embed(self: AgentRagClient, texts: list[str]) -> list[list[float]]:
        state["embed"] += 1
        return [[0.0] * 1536 for _ in texts]

    async def fake_describe(
        self: AgentRagClient, block: object, schema_name: str
    ) -> tuple[bool, object]:
        state["describe"] += 1
        return False, block

    monkeypatch.setattr(AgentRagClient, "embed", fake_embed)
    monkeypatch.setattr(AgentRagClient, "describe", fake_describe)
    return state


@pytest.fixture(scope="module", autouse=True)
async def _biz() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """default 源业务表:goods(将登记)+ stock(保持零覆盖)。"""
    from kongming.db import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS tenant_default"))
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tenant_default.goods"
                " (id int primary key, name text, create_by uuid)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tenant_default.stock"
                " (id int primary key, note text)"
            )
        )
    yield
    async with get_engine().begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS tenant_default.goods"))
        await conn.execute(text("DROP TABLE IF EXISTS tenant_default.stock"))
        await conn.execute(text("DROP SCHEMA IF EXISTS tenant_default CASCADE"))


async def _seed_entry(
    session: AsyncSession,
    tenant_id: str,
    table: str,
    *,
    ds: str = "default",
    source: str = "AI_GENERATED",
    schema: str = "tenant_default",
    columns_comment: str = "{}",
    sample_queries: str = "[]",
) -> None:
    await session.execute(
        text(
            "INSERT INTO rag.table_descriptions"
            " (id, tenant_id, datasource_key, schema_name, table_name,"
            "  table_comment, columns_comment, sample_queries, source_type)"
            " VALUES (gen_random_uuid(), :t, :d, :sn, :n, 'c',"
            "  CAST(:cols AS jsonb), CAST(:samples AS jsonb), :st)"
        ),
        {"t": tenant_id, "d": ds, "sn": schema, "n": table, "cols": columns_comment,
         "samples": sample_queries, "st": source},
    )
    await session.commit()


async def _seed_session_msg(
    session: AsyncSession,
    *,
    tenant_id: str,
    datasource_key: str,
    question: str,
    status: str,
    rating: str | None = None,
    corrected: str | None = None,
    age: timedelta | None = None,
) -> None:
    """直插一条助手消息(失败信号 arrange;user 取 admin)。"""
    uid = (
        await session.execute(text("SELECT id FROM public.sys_user WHERE username='admin'"))
    ).scalar_one()
    age_sql = f"- interval '{int((age or timedelta()).total_seconds())} seconds'"
    sid = (
        await session.execute(
            text(
                "INSERT INTO public.chat_session"
                " (id, user_id, tenant_id, datasource_key)"
                " VALUES (gen_random_uuid(), :u, :t, :d) RETURNING id"
            ),
            {"u": str(uid), "t": tenant_id, "d": datasource_key},
        )
    ).scalar_one()
    await session.execute(
        text(
            # S608 豁免:age_sql 为测试内常量算式(timedelta→interval),非外部输入
            "INSERT INTO public.chat_message"  # noqa: S608
            " (id, session_id, role, question, status, rating, corrected_sql, created_at)"
            " VALUES (gen_random_uuid(), :s, 'ASSISTANT', :q, :st, :rating,"
            f" :corrected, now() {age_sql})"
        ),
        {"s": str(sid), "q": question, "st": status, "rating": rating, "corrected": corrected},
    )
    await session.commit()


async def _seed_history(session: AsyncSession, tenant_id: str, question: str) -> None:
    await session.execute(
        text(
            "INSERT INTO rag.query_history"
            " (id, tenant_id, datasource_key, question, sql_text, status)"
            " VALUES (gen_random_uuid(), :t, 'default', :q, 'SELECT 1', 'SUCCESS')"
        ),
        {"t": tenant_id, "q": question},
    )
    await session.commit()


async def _get_report(client: AsyncClient, token: str) -> tuple[int, dict[str, Any]]:
    r = await client.get(f"{RAG}/coverage-report", params={"datasource_key": "default"},
                         headers=_auth(token))
    return r.status_code, cast("dict[str, Any]", r.json())


async def test_coverage_report_no_perm_403(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser
) -> None:
    token = await _noperm_user(client, session, make_user)
    r = await client.get(f"{RAG}/coverage-report", params={"datasource_key": "default"},
                         headers=_auth(token))
    assert r.status_code == 403


async def test_coverage_report_bad_datasource_same_shape_400(
    client: AsyncClient, session: AsyncSession
) -> None:
    token = await _admin_token(client, session)
    r = await client.get(f"{RAG}/coverage-report", params={"datasource_key": "no_such"},
                         headers=_auth(token))
    assert r.status_code == 400
    assert r.json()["detail"] == "DATASOURCE_INVALID"


async def test_coverage_report_happy_path(
    client: AsyncClient, session: AsyncSession, counting_agent: dict[str, int]
) -> None:
    token = await _admin_token(client, session)
    mine = await _default_tenant_id(session)
    # 登记:goods 缺 create_by 标签+示例 SQL 空;legacy 纯结构降级
    await _seed_entry(
        session, mine, "goods",
        columns_comment='{"id": "int 主键", "name": "名称"}',
    )
    await _seed_entry(session, mine, "legacy", source="INTROSPECTION")
    # few-shot 概况:2 条成功沉淀
    await _seed_history(session, mine, "每个门店的库存数量")
    await _seed_history(session, mine, "各分类的销售额")
    # 失败信号:同题归并 ×2(最近 NO_DATA)/差评/跨租户不可见/窗外不收
    await _seed_session_msg(
        session, tenant_id=mine, datasource_key="default",
        question="每个门店的库存数量", status="FAILED",
        age=timedelta(hours=1),
    )
    await _seed_session_msg(
        session, tenant_id=mine, datasource_key="default",
        question=" 每个门店的库存数量", status="NO_DATA",
    )
    await _seed_session_msg(
        session, tenant_id=mine, datasource_key="default",
        question="帮我导出全部订单", status="SUCCESS", rating="DOWN",
    )
    await _seed_session_msg(
        session, tenant_id=mine, datasource_key="default",
        question="31 天前的失败题", status="FAILED", age=timedelta(days=31),
    )
    from tests.test_datasource_admin import _mk_tenant

    other = await _mk_tenant(session, "covothert")
    await _seed_session_msg(
        session, tenant_id=other, datasource_key="default",
        question="他租户的失败题", status="FAILED",
    )

    code, body = await _get_report(client, token)
    assert code == 200
    assert body["datasource_key"] == "default"
    # 零覆盖表:stock 未登记;goods/legacy 已登记
    unreg = [(t["schema_name"], t["table_name"]) for t in body["unregistered_tables"]]
    assert ("tenant_default", "stock") in unreg
    assert ("tenant_default", "goods") not in unreg
    assert body["unregistered_total"] == len(unreg)
    # 纯结构登记:legacy(INTROSPECTION)
    assert [(t["table_name"]) for t in body["structure_only_tables"]] == ["legacy"]
    # 缺标签列:goods 缺 create_by
    mc = body["missing_columns"]
    assert len(mc) == 1
    assert mc[0]["table_name"] == "goods"
    assert mc[0]["missing"] == ["create_by"]
    assert mc[0]["missing_count"] == 1
    # 示例缺口:goods/legacy 示例 SQL 均空
    assert {t["table_name"] for t in body["no_sample_tables"]} == {"goods", "legacy"}
    # few-shot 概况
    assert body["history"] == {"success_count": 2, "distinct_questions": 2, "cap": 500}
    # 失败问题簇:同题归并 ×2(最近 NO_DATA)+差评 1;窗外/他租户/成功不收
    clusters = body["failure_clusters"]
    assert body["failure_cluster_total"] == 2
    merged = next(c for c in clusters if "每个门店" in c["question"])
    assert merged["count"] == 2
    assert merged["last_status"] == "NO_DATA"
    assert all("31 天前" not in c["question"] for c in clusters)
    assert all("他租户" not in c["question"] for c in clusters)
    # 零 LLM 调用(纯聚合契约)
    assert counting_agent["embed"] == 0
    assert counting_agent["describe"] == 0


async def test_coverage_report_introspect_failure_no_partial(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kongming.rag.repository import RagRepository

    token = await _admin_token(client, session)

    async def broken_introspect(self: RagRepository, **kw: object) -> list[object]:
        raise LookupError("default")

    monkeypatch.setattr(RagRepository, "introspect_all", broken_introspect)
    before = (
        await session.execute(text("SELECT COUNT(*) FROM rag.table_descriptions"))
    ).scalar_one()
    code, _body = await _get_report(client, token)
    assert code == 400
    after = (
        await session.execute(text("SELECT COUNT(*) FROM rag.table_descriptions"))
    ).scalar_one()
    assert before == after  # 不产出半份报告,既有数据零改动


async def test_coverage_report_deterministic_readonly(
    client: AsyncClient, session: AsyncSession, counting_agent: dict[str, int]
) -> None:
    token = await _admin_token(client, session)
    code1, body1 = await _get_report(client, token)
    code2, body2 = await _get_report(client, token)
    assert code1 == code2 == 200
    assert body1 == body2  # 确定性:同库态两次一致
    assert counting_agent["embed"] == 0
    assert counting_agent["describe"] == 0
