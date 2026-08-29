"""rag 纯函数测试(009):切块解析/RRF/优先级/提示构造(embedding 文本公式)。"""

from typing import Any

from kongming.rag.models import (
    KnowledgeEntry,
    RecallHit,
    TableBlock,
    build_embedding_text,
    build_history_sql,
    build_schema_hint,
    can_automated_overwrite,
    rrf_fuse,
)
from kongming.rag.parser import columns_to_json, parse_markdown

# ============ 切块解析 ============


def test_parse_basic_blocks() -> None:
    md = """# 库表说明

## customer
说明:客户表
字段:id 主键, email 邮箱, name 姓名
示例:SELECT count(*) FROM t.customer;

## film
说明:影片表
"""
    blocks = parse_markdown(md)
    assert [b.table_name for b in blocks] == ["customer", "film"]
    c = blocks[0]
    assert c.table_comment == "客户表"
    assert c.columns_comment == {"id": "主键", "email": "邮箱", "name": "姓名"}
    assert c.sample_queries == ["SELECT count(*) FROM t.customer"]
    assert blocks[1].table_comment == "影片表"


def test_parse_schema_dotted_header() -> None:
    """`## schema.table` 形态:标题 schema 段不落库(schema 是表单维度)。"""
    blocks = parse_markdown("## sakila.customer\n说明:客户")
    assert blocks[0].table_name == "customer"


def test_parse_folds_table_name() -> None:
    """表名小写折叠(PG 未加引号标识符语义,`## Customer`→customer)。"""
    blocks = parse_markdown("## Customer\n说明:客户")
    assert blocks[0].table_name == "customer"


def test_parse_same_name_last_wins() -> None:
    md = "## goods\n说明:旧\n\n## goods\n说明:新"
    blocks = parse_markdown(md)
    assert len(blocks) == 1 and blocks[0].table_comment == "新"


def test_parse_no_blocks_raises() -> None:
    import pytest

    from kongming.rag.models import EmptyDocument

    with pytest.raises(EmptyDocument):
        parse_markdown("# 只有H1\n正文")


def test_columns_fullwidth_comma_split() -> None:
    assert columns_to_json("id 主键，name 姓名") == {"id": "主键", "name": "姓名"}


def test_columns_raw_fallback() -> None:
    """全部项解析不出列名时兜底 _raw(内容不丢)。"""
    assert columns_to_json("((( )))") == {"_raw": "((( )))"}


def test_samples_split_by_semicolon() -> None:
    blocks = parse_markdown("## t\n示例:SELECT 1; SELECT 2")
    assert blocks[0].sample_queries == ["SELECT 1", "SELECT 2"]


# ============ RRF 融合 ============


def test_rrf_exact_hit_promoted() -> None:
    """bed_bath_table 精确名经全文路修正排序(spec 场景):两路均含时融合排名
    优于纯向量单路。"""
    vec_only = [RecallHit(table_name="customer"), RecallHit(table_name="bed_bath_table")]
    fulltext_only = [RecallHit(table_name="bed_bath_table")]
    fused = rrf_fuse([vec_only, fulltext_only])
    assert fused[0].table_name == "bed_bath_table"


def test_rrf_single_list_degrades() -> None:
    hits = [RecallHit(table_name="a"), RecallHit(table_name="b")]
    assert [h.table_name for h in rrf_fuse([hits])] == ["a", "b"]


def test_rrf_top_limit() -> None:
    hits = [RecallHit(table_name=f"t{i}") for i in range(8)]
    assert len(rrf_fuse([hits])) == 5


def test_rrf_empty_lists() -> None:
    assert rrf_fuse([[], []]) == []


# ============ 覆盖优先级 ============


def test_priority_ordering() -> None:
    assert can_automated_overwrite("INTROSPECTION", "AI_GENERATED")  # 同为自动,升级可覆盖
    assert can_automated_overwrite("AI_GENERATED", "AI_GENERATED")  # 同级幂等更新
    assert not can_automated_overwrite("MANUAL_UPLOAD", "AI_GENERATED")  # 自动不覆盖人工
    assert not can_automated_overwrite("MANUAL_EDIT", "INTROSPECTION")
    assert not can_automated_overwrite("FEEDBACK_REFINED", "AI_GENERATED")


# ============ 提示构造 ============


def _entry(**over: object) -> KnowledgeEntry:
    base: dict[str, Any] = {
        "id": "x",
        "tenant_id": "t",
        "datasource_key": "default",
        "schema_name": "tenant_sakila",
        "table_name": "film",
        "table_comment": "影片表",
        "columns_comment": {"film_id": "integer 影片ID", "title": ""},
        "sample_queries": ["SELECT count(*) FROM tenant_sakila.film"],
        "source_type": "AI_GENERATED",
        "updated_at": None,
    }
    base.update(over)
    return KnowledgeEntry(**base)


def test_build_schema_hint_shape() -> None:
    hint = build_schema_hint([_entry()])
    assert "tenant_sakila.film(film_id(integer 影片ID), title) -- 影片表" in hint
    assert "示例:SELECT count(*) FROM tenant_sakila.film" in hint


def test_build_schema_hint_alias_shape() -> None:
    """037:alias 进 schema_hint 表说明段「表名(别名)(列)」;空 alias 形态零回归。"""
    with_alias = build_schema_hint([_entry(alias="影片信息表")])
    assert "tenant_sakila.film(影片信息表)(film_id(integer 影片ID), title) -- 影片表" in (
        with_alias
    )
    # 空 alias:与引入前逐字一致(存量零回归)
    assert build_schema_hint([_entry()]) == (
        "tenant_sakila.film(film_id(integer 影片ID), title) -- 影片表\n"
        "  示例:SELECT count(*) FROM tenant_sakila.film"
    )


def test_build_history_sql_limit() -> None:
    assert build_history_sql(["SELECT 1", "SELECT 2", "SELECT 3", "SELECT 4"]) == (
        "SELECT 1\nSELECT 2\nSELECT 3"
    )


def test_embedding_text_formula() -> None:
    """embedding 文本=表名+表说明+字段说明(上传/扫描/润色三处同一公式)。"""
    text = build_embedding_text(
        "film", "影片表", {"film_id": "integer 影片ID", "title": "varchar 片名"}
    )
    assert text == "film 影片表 film_id integer 影片ID title varchar 片名"


def test_embedding_text_empty_parts() -> None:
    assert build_embedding_text("goods", "", {}) == "goods"


def test_embedding_text_includes_alias() -> None:
    """037:alias 拼 embedding 输入文本(输入侧「商品表」命中 alias);无 alias 不变形。"""
    text = build_embedding_text("goods", "商品信息", {}, alias="商品表")
    assert text == "goods 商品表 商品信息"
    assert build_embedding_text("goods", "商品信息", {}) == "goods 商品信息"


def test_clean_alias_defense() -> None:
    """R1-F1 纵深:结构性违禁字符(括号/换行/竖线)置空;空白折叠。"""
    from kongming.rag.models import clean_alias

    assert clean_alias("  商品 表 ") == "商品 表"      # 折叠空白
    assert clean_alias("商品表") == "商品表"
    assert clean_alias("a\nfake.table(q)") == ""       # 伪表名注入形态
    assert clean_alias("商品(表)") == ""               # ASCII 括号
    assert clean_alias("商品（表）") == ""              # 全角括号
    assert clean_alias("商品|表") == ""


def test_build_schema_hint_dirty_alias_sanitized() -> None:
    """R1-F1 纵深:脏别名不进 hint 行结构(整体按无别名处理)。"""
    hint = build_schema_hint([_entry(alias="bad\nfake.table(q)")])
    assert hint.startswith("tenant_sakila.film(film_id(integer 影片ID), title)")


def test_table_block_frozen() -> None:
    b = TableBlock(table_name="t")
    assert b.table_comment == "" and b.columns_comment == {} and b.sample_queries == []
