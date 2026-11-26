"""覆盖结果列标签的 SQL 溯源和问答落库回显。"""
# pyright: reportPrivateUsage=false

import json
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.chat.dependencies import get_agent_client
from kongming.chat.models import humanize_stats, relabel_column_mentions, resolve_column_labels
from kongming.main import app
from tests.conftest import MakeUser
from tests.test_chat_query import FakeAgent, _auth, _pass_reply, _user_with_chat_perm

# ============ 纯函数(SQL 投影溯源) ============

_COMMENTS = {
    "customer": {
        "customer_id": "integer 客户唯一标识符",
        "first_name": "character varying 客户名字",
        "last_name": "character varying 客户姓氏",
    },
    "payment": {
        "customer_id": "integer 关联客户编号",
        "amount": "numeric 支付金额",
    },
}


def test_qualified_column_resolves_via_alias() -> None:
    # JOIN 下 c.customer_id 明确来自 customer 表→精确取该表注释
    sql = (
        "SELECT c.customer_id, c.first_name, SUM(p.amount) AS total"
        " FROM customer c JOIN payment p ON p.customer_id = c.customer_id"
        " GROUP BY c.customer_id, c.first_name"
    )
    labels = resolve_column_labels(["customer_id", "first_name", "total"], _COMMENTS, sql)
    assert labels == ["客户唯一标识符", "客户名字", "total"]


def test_bare_column_unique_table_wins() -> None:
    # 裸列名在全部涉及表中唯一出现(amount 仅 payment 有)→用之
    sql = "SELECT customer_id, amount FROM payment"
    assert resolve_column_labels(["customer_id", "amount"], _COMMENTS, sql) == [
        "关联客户编号",
        "支付金额",
    ]


def test_bare_column_multi_table_conflict_falls_back() -> None:
    # 裸列跨表语义冲突(customer/payment 的 customer_id 注释不同)→回退
    sql = (
        "SELECT customer_id, first_name FROM customer"
        " JOIN payment ON payment.customer_id = customer.customer_id"
    )
    labels = resolve_column_labels(["customer_id", "first_name"], _COMMENTS, sql)
    assert labels == ["customer_id", "客户名字"]


def test_expression_and_unknown_fall_back() -> None:
    # 表达式别名(total)与无注释列(mystery)回退物理名——宁缺毋错
    sql = "SELECT SUM(amount) AS total, mystery FROM payment"
    assert resolve_column_labels(["total", "mystery"], _COMMENTS, sql) == [
        "total",
        "mystery",
    ]


def test_unparseable_sql_falls_back_entirely() -> None:
    labels = ["a", "b"]
    assert resolve_column_labels(labels, _COMMENTS, "这是一段不是 SQL 的文本") == ["a", "b"]
    assert resolve_column_labels(["a"], _COMMENTS, "") == ["a"]
    assert resolve_column_labels(["a"], {}, "SELECT 1") == ["a"]


def test_non_cjk_comment_treated_as_miss() -> None:
    # 无中文可剥(纯类型/纯英文)不构成标签——防「integer」半剥产物入界面
    comments = {"rental": {"customer_id": "integer", "title": "Film title"}}
    sql = "SELECT customer_id, title FROM rental"
    assert resolve_column_labels(["customer_id", "title"], comments, sql) == [
        "customer_id",
        "title",
    ]


def test_type_prefix_stripped() -> None:
    # 带类型前缀的注释剥出纯中文业务名
    sql = "SELECT name, qty FROM goods"
    comments = {"goods": {"name": "varchar 商品名", "qty": "integer 库存数量"}}
    assert resolve_column_labels(["name", "qty"], comments, sql) == ["商品名", "库存数量"]


def test_computed_labels_follow_question_wording() -> None:
    # 计算列不能直接暴露 SQL 别名:按投影结构和用户问句取业务短语
    comments = {
        "ledger": {
            "store_id": "integer 门店编号",
            "booked_at": "timestamp 支付时间",
            "amount": "numeric 支付金额",
        }
    }
    sql = (
        "SELECT DATE_TRUNC('month', booked_at)::DATE AS booked_month, store_id,"
        " COUNT(*) AS n, SUM(amount) AS total FROM ledger GROUP BY 1, 2"
    )
    labels = resolve_column_labels(
        ["booked_month", "store_id", "n", "total"],
        comments,
        sql,
        question="按支付月份统计每个门店的支付笔数和总支付金额",
    )
    assert labels == ["支付月份", "门店编号", "支付笔数", "总支付金额"]


def test_computed_labels_use_resolved_question_for_follow_up() -> None:
    # 追问原句只带时间口径;指标短语从同轮意图兜底,仍不暴露别名
    comments = {
        "ledger": {
            "store_id": "integer 门店编号",
            "booked_at": "timestamp 租借时间",
            "amount": "numeric 支付金额",
        }
    }
    sql = (
        "SELECT DATE_TRUNC('month', booked_at)::DATE AS booked_month, store_id,"
        " COUNT(DISTINCT rental_id) AS n, SUM(amount) AS total FROM ledger GROUP BY 1, 2"
    )
    labels = resolve_column_labels(
        ["booked_month", "store_id", "n", "total"],
        comments,
        sql,
        question="按租赁月份。",
        resolved_question="按租赁单的租借月份统计每个门店的租赁笔数和总支付金额。",
    )
    assert labels == ["租赁月份", "门店编号", "租赁笔数", "总支付金额"]


def test_temporal_phrase_keeps_period_modifier_and_measure_word() -> None:
    """「最近三个月」不能被单字截断成「近三月」;「每个」仍按连接词切开。"""
    sql = (
        "SELECT DATE_TRUNC('month', booked_at)::DATE AS booked_month, store_id,"
        " COUNT(*) AS n FROM ledger GROUP BY 1, 2"
    )
    labels = resolve_column_labels(
        ["booked_month", "store_id", "n"],
        {},
        sql,
        question="最近三个月每个门店的支付笔数",
    )
    assert labels == ["最近三个月", "store_id", "支付笔数"]


def test_computed_labels_without_comments_still_use_question() -> None:
    # 无数据源注释的计算列也可从问句取标签;纯列保持物理名(fail-open)
    sql = (
        "SELECT DATE_TRUNC('month', booked_at)::DATE AS booked_month, store_id,"
        " COUNT(*) AS n, SUM(amount) AS total FROM ledger GROUP BY 1, 2"
    )
    labels = resolve_column_labels(
        ["booked_month", "store_id", "n", "total"],
        {},
        sql,
        question="按月统计每个门店的支付笔数和总支付金额",
    )
    assert labels == ["月份", "store_id", "支付笔数", "总支付金额"]


def test_multi_metric_labels_align_by_projection() -> None:
    """同类投影多个时按投影顺序取问句候选,不把所有列贴成同一个名字。"""
    sql = (
        "SELECT store_id, COUNT(DISTINCT rental_id) AS rental_cnt,"
        " COUNT(DISTINCT payment_id) AS payment_cnt FROM ledger GROUP BY store_id"
    )
    labels = resolve_column_labels(
        ["store_id", "rental_cnt", "payment_cnt"],
        {},
        sql,
        question="每个门店的租赁笔数和支付笔数",
    )
    assert labels == ["store_id", "租赁笔数", "支付笔数"]
    stats = humanize_stats(
        {"rows": 2, "rental_cnt_max": 30, "payment_cnt_max": 90},
        ["store_id", "rental_cnt", "payment_cnt"],
        labels,
    )
    assert stats["租赁笔数 最高"] == 30
    assert stats["支付笔数 最高"] == 90


def test_multi_amount_labels_align_by_projection() -> None:
    sql = "SELECT SUM(amount) AS total, AVG(amount) AS avg_amt FROM ledger"
    labels = resolve_column_labels(
        ["total", "avg_amt"],
        {"ledger": {"amount": "numeric 支付金额"}},
        sql,
        question="支付总额和平均支付金额",
    )
    assert labels == ["支付总额", "平均支付金额"]


def test_ratio_projection_is_not_misclassified_as_count() -> None:
    sql = "SELECT COUNT(*) FILTER (WHERE status = 'paid') * 1.0 / COUNT(*) AS rate FROM ledger"
    assert resolve_column_labels(["rate"], {}, sql, question="支付笔数占比") == ["rate"]


def test_conflicting_computed_labels_fall_back_to_physical_names() -> None:
    """同类候选不足时宁缺毋错,不能把多列贴成同一个标签。"""
    sql = (
        "SELECT COUNT(DISTINCT rental_id) AS rental_cnt,"
        " COUNT(DISTINCT payment_id) AS payment_cnt FROM ledger"
    )
    labels = resolve_column_labels(["rental_cnt", "payment_cnt"], {}, sql, question="支付笔数")
    assert labels == ["rental_cnt", "payment_cnt"]


def test_relabel_column_mentions_uses_business_labels() -> None:
    text = "共 10 行；booked_month 为 2005-07-01 的 n 最高（3365），2006-02-01 最低（87）。"
    assert (
        relabel_column_mentions(
            text,
            ["booked_month", "store_id", "n", "total"],
            ["支付月份", "门店编号", "支付笔数", "总支付金额"],
        )
        == "共 10 行；支付月份 为 2005-07-01 的 支付笔数 最高（3365），2006-02-01 最低（87）。"
    )


def test_relabel_guarded_applies_to_summary_and_insights() -> None:
    from kongming.chat.finalize import _Guarded, _relabel_guarded

    guarded = _Guarded(
        summary="共 2 行；n 最高（30）",
        insights=[{"type": "max", "text": "total 最高为 30"}],
        sql_text="SELECT 1",
        error_raw=None,
    )
    out = _relabel_guarded(guarded, ["n", "total"], ["支付笔数", "总支付金额"])
    assert out.summary == "共 2 行；支付笔数 最高（30）"
    assert out.insights == [{"type": "max", "text": "总支付金额 最高为 30"}]
    assert out.sql_text == "SELECT 1"


def test_length_alignment_and_empty_tables() -> None:
    assert resolve_column_labels(["a", "b"], {}, "SELECT a, b FROM t") == ["a", "b"]


def test_star_projection_falls_back_entirely() -> None:
    # 星号投影一对多(AST 投影数≠结果列数):按位对位会把后续投影的标签
    # 贴到星号展开列上——SELECT *, amount 第 2 列错得「支付金额」。
    # 宁缺毋错:整体回退物理名(纯 SELECT * 同口径)。
    sql = "SELECT *, amount FROM payment"
    labels = resolve_column_labels(["customer_id", "amount", "amount"], _COMMENTS, sql)
    assert labels == ["customer_id", "amount", "amount"]
    assert resolve_column_labels(["customer_id", "amount"], _COMMENTS, "SELECT * FROM payment") == [
        "customer_id",
        "amount",
    ]


# ============ 全栈(真实 rag repo;agent 替身) ============


@pytest.fixture
def labels_agent() -> Iterator[dict[str, Any]]:
    """scripted agent 替身(自持装配):成功应答带 columns/rows。"""
    holder: dict[str, Any] = {}

    def _factory() -> Any:
        return holder["agent"]

    app.dependency_overrides[get_agent_client] = _factory
    holder["agent"] = FakeAgent(_pass_reply())
    yield holder
    app.dependency_overrides.pop(get_agent_client, None)


async def _ask(client: AsyncClient, token: str) -> dict[str, object]:
    r = await client.post("/chat/query", json={"question": "各商品库存"}, headers=_auth(token))
    assert r.status_code == 200
    return r.json()


async def test_labels_fallback_then_hit_with_persist(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    labels_agent: dict[str, Any],
) -> None:
    """无注释行→回退原列名;插入注释→业务标签;落库 result_json 同源携带。"""
    _, token = await _user_with_chat_perm(session, make_user, "qa40")
    tid = await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    tenant_id = str(tid.scalar_one())

    # 先删后插(共享测试库幂等):本测试插入的 goods 注释行不得残留污染重跑
    await session.execute(
        text(
            "DELETE FROM rag.table_descriptions"
            " WHERE tenant_id=:t AND datasource_key='default'"
            " AND schema_name='tenant_default' AND table_name='goods'"
        ),
        {"t": tenant_id},
    )
    await session.commit()

    # 阶段一:无注释行→回退原列名(物理名,等长对齐)
    body = await _ask(client, token)
    assert body["columns"] == ["name", "qty"]
    assert body["column_labels"] == ["name", "qty"]

    # 阶段二:插入 goods 列注释→命中业务标签
    await session.execute(
        text(
            "INSERT INTO rag.table_descriptions"
            " (id, tenant_id, datasource_key, schema_name, table_name,"
            "  table_comment, columns_comment, sample_queries, source_type)"
            " VALUES (gen_random_uuid(), :t, 'default', 'tenant_default', 'goods',"
            " '商品表', :cols, '[]'::jsonb, 'AI_GENERATED')"
        ),
        {"t": tenant_id, "cols": '{"name": "varchar 商品名", "qty": "integer 库存数量"}'},
    )
    await session.commit()

    body = await _ask(client, token)
    assert body["column_labels"] == ["商品名", "库存数量"]

    # 落库同源:assistant 消息 result_json 携带 column_labels(历史回显读档即事实源)
    row = (
        await session.execute(
            text(
                "SELECT result_json FROM public.chat_message"
                " WHERE role='ASSISTANT' ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).scalar_one()
    stored = json.loads(str(row))
    assert stored["column_labels"] == ["商品名", "库存数量"]
    assert stored["columns"] == ["name", "qty"]  # 物理名恒不动


async def test_column_comments_tenant_schema_overrides_public(
    client: AsyncClient,
    session: AsyncSession,
    make_user: MakeUser,
    labels_agent: dict[str, Any],
) -> None:
    """同名表跨 schema(default 源 tenant_<key> 与 public 并存):注释取
    租户 schema 胜出——覆盖方向必须确定,不得随查询计划漂移。"""
    from kongming.rag.repository import RagRepository

    # 播种(_clean 每用例清身份表,default 租户由播种链重建)
    await _user_with_chat_perm(session, make_user, "qa41x")
    tid = str(
        (
            await session.execute(
                text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'")
            )
        ).scalar_one()
    )
    # 先删后插(共享测试库幂等):public 与 tenant_default 各一行同名表
    await session.execute(
        text(
            "DELETE FROM rag.table_descriptions"
            " WHERE tenant_id=:t AND datasource_key='default'"
            " AND table_name='goods' AND schema_name IN ('public', 'tenant_default')"
        ),
        {"t": tid},
    )
    await session.execute(
        text(
            "INSERT INTO rag.table_descriptions"
            " (id, tenant_id, datasource_key, schema_name, table_name,"
            "  table_comment, columns_comment, source_type)"
            " VALUES (gen_random_uuid(), :t, 'default', 'public', 'goods',"
            " '公共商品表', '{\"name\": \"varchar 公共名\"}', 'MANUAL_UPLOAD'),"
            "        (gen_random_uuid(), :t, 'default', 'tenant_default', 'goods',"
            " '租户商品表', '{\"name\": \"varchar 租户名\"}', 'MANUAL_UPLOAD')"
        ),
        {"t": tid},
    )
    await session.commit()
    comments = await RagRepository(session).entries.column_comments(
        tenant_id=tid, datasource_key="default"
    )
    assert comments["goods"] == {"name": "varchar 租户名"}  # 租户 schema 胜出
    # 清理 goods 测试数据，避免影响其他用例。
    await session.execute(
        text(
            "DELETE FROM rag.table_descriptions"
            " WHERE tenant_id=:t AND datasource_key='default'"
            " AND table_name='goods' AND schema_name IN ('public', 'tenant_default')"
        ),
        {"t": tid},
    )
    await session.commit()
