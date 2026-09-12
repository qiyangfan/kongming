"""行级改写测试:与消费方 datascope.rewrite 内置测试资产同源(一比一移植的自证)。"""

from liannu import parse


def test_user_eq_injects_outer_where() -> None:
    art = parse("SELECT mark FROM customer")
    sql, params = art.apply_scope_filter(filter_type="USER_EQ", column="create_by", values=["u-1"])
    assert "WHERE" in sql and "customer.create_by = :km_ds_0" in sql
    assert "mark" in sql  # 原投影不受损
    assert params == {"km_ds_0": "u-1"}
    assert "u-1" not in sql  # 值不出现在 SQL 文本(绑定参数非拼接)


def test_existing_where_and_combined() -> None:
    art = parse("SELECT * FROM customer c WHERE c.id > 1")
    sql, _ = art.apply_scope_filter(filter_type="USER_EQ", column="create_by", values=["u"])
    assert "c.id > 1 AND c.create_by = :km_ds_0" in sql


def test_dept_in_params() -> None:
    art = parse("SELECT * FROM customer")
    sql, params = art.apply_scope_filter(
        filter_type="DEPT_IN", column="dept_id", values=["d1", "d2"]
    )
    assert "IN (:km_ds_0, :km_ds_1)" in sql
    assert params == {"km_ds_0": "d1", "km_ds_1": "d2"}


def test_skip_returns_original() -> None:
    art = parse("SELECT * FROM customer")
    sql, params = art.apply_scope_filter(filter_type="SKIP", column=None, values=[])
    assert sql == "SELECT * FROM customer"
    assert params == {}
    sql2, _ = art.apply_scope_filter(filter_type="UNSUPPORTED", column=None, values=[])
    assert sql2 == "SELECT * FROM customer"


def test_complex_and_subquery_forms() -> None:
    art = parse(
        "SELECT c.name, COUNT(r.id) FROM customer c "
        "JOIN rental r ON c.id = r.customer_id GROUP BY c.name"
    )
    sql, _ = art.apply_scope_filter(filter_type="DEPT_IN", column="dept_id", values=["d1"])
    assert "c.dept_id IN (:km_ds_0)" in sql

    art2 = parse(
        "SELECT c.name FROM customer c WHERE c.id IN (SELECT customer_id FROM rental WHERE id > 10)"
    )
    sql2, params2 = art2.apply_scope_filter(filter_type="DEPT_IN", column="dept_id", values=["d1"])
    assert "IN (SELECT" in sql2  # 原子查询原样保留
    assert "c.dept_id IN (:km_ds_0)" in sql2  # 谓词限定主表别名,在最外层
    assert params2 == {"km_ds_0": "d1"}


def test_main_table_ref() -> None:
    assert parse("SELECT * FROM customer").main_table_ref() == ("customer", None)
    assert parse("SELECT * FROM public.customer").main_table_ref() == ("customer", "public")
    assert parse("SELECT c.id FROM public.customer c JOIN x ON 1=1").main_table_ref() == (
        "customer",
        "public",
    )
    assert parse("SELECT * FROM (SELECT 1 FROM a) t").main_table_ref() is None
    assert parse("SELECT 1 UNION SELECT 2").main_table_ref() is None
    assert parse("").main_table_ref() is None
    assert parse("SELECT FROM WHERE").main_table_ref() is None
