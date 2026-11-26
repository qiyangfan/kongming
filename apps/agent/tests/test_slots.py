"""槽位契约:注册、校验、渲染与确定性兜底。"""

from __future__ import annotations

from datetime import UTC, datetime

from kongming_agent.graph import slots


def _rating_case() -> tuple[list[str], list[list[object]], dict[str, int | float]]:
    columns = ["rating", "count"]
    rows: list[list[object]] = [
        ["PG-13", 223],
        ["NC-17", 210],
        ["R", 195],
        ["PG", 194],
        ["G", 178],
    ]
    stats = {
        "rows": 5,
        "count_max": 223,
        "count_min": 178,
        "count_avg": 200.0,
    }
    return columns, rows, stats


class TestFormat:
    def test_number(self) -> None:
        assert slots.format_number(0) == "0"
        assert slots.format_number(14003.5) == "14003.5"
        assert slots.format_number(14003.546) == "14003.55"
        assert slots.format_number(2.0) == "2"

    def test_cell(self) -> None:
        assert slots.format_cell(None) == "空"
        assert slots.format_cell(True) == "是"
        assert slots.format_cell(datetime(2005, 7, 1, 12, 0, tzinfo=UTC)) == "2005-07-01"
        assert slots.format_cell("PG-13") == "PG-13"


class TestRegistry:
    def test_stats_argmax_label_and_cell(self) -> None:
        columns, rows, stats = _rating_case()
        reg = slots.build_registry(
            question="统计每个评级的影片数量", columns=columns, rows=rows, stats=stats
        )
        assert reg.resolve("rows") == "5"
        assert reg.resolve("stats.count_max") == "223"
        assert reg.resolve("argmax.count.value") == "223"
        assert reg.resolve("argmax.count.label") == "PG-13"
        assert reg.resolve("argmin.count.label") == "G"
        assert reg.resolve("cell.0.0") == "PG-13"
        assert reg.resolve("cell.4.1") == "178"
        assert reg.resolve("cell.9.0") is None
        assert reg.resolve("cell.0.5") is None

    def test_question_premise_and_alias_and_nulls(self) -> None:
        columns = ["amount", "email"]
        rows: list[list[object]] = [[120.5, "a@b.c"], [None, None]]
        stats = {"rows": 2, "amount_max": 120.5, "amount_min": 120.5, "amount_avg": 120.5}
        reg = slots.build_registry(
            question="单笔支付金额超过100美元的订单有多少条",
            columns=columns,
            rows=rows,
            stats=stats,
            compute_slots={"delta_pct": 25.0},
            aliases=[("支付流水", "public.payment"), ("", "public.film")],
        )
        assert reg.resolve("q.0") == "100"
        assert reg.resolve("calc.delta_pct") == "25%"
        assert reg.resolve("alias.0") == "支付流水"
        assert reg.resolve("alias.0.physical") == "public.payment"
        assert reg.resolve("alias.1") is None
        assert reg.resolve("alias.1.physical") == "public.film"
        assert reg.resolve("nulls.email") == "1"
        assert reg.resolve("nulls.amount") == "1"

    def test_prompt_block_lists_keys_and_cell_pattern(self) -> None:
        columns, rows, stats = _rating_case()
        reg = slots.build_registry(question="q", columns=columns, rows=rows, stats=stats)
        block = reg.prompt_block()
        assert "{{stats.count_max}}" in block
        assert "{{argmax.count.label}}" in block
        assert "{{cell.<行号>.<列号>}}" in block
        assert "禁止出现任何阿拉伯数字" in block

    def test_stats_pct_keys_render_percent(self) -> None:
        reg = slots.build_registry(
            question="q",
            columns=["v"],
            rows=[[1], [2]],
            stats={"rows": 2, "v_delta_pct": 25.0, "v_share_pct": 33.3333},
        )
        assert reg.resolve("stats.v_delta_pct") == "25%"
        assert reg.resolve("stats.v_share_pct") == "33.33%"

    def test_scoped_registry_prefixes_keys(self) -> None:
        columns, rows, stats = _rating_case()
        reg = slots.build_registry(
            question="q", columns=columns, rows=rows, stats=stats, scope="r0"
        )
        assert reg.resolve("r0.rows") == "5"
        assert reg.resolve("r0.cell.0.0") == "PG-13"
        assert reg.resolve("rows") is None
        assert "{{r0.stats.count_max}}" in reg.prompt_block()

    def test_result_pct_column_keeps_raw_value(self) -> None:
        """结果列名不推断单位:share_pct 的 25 原样渲染,不擅自补 %。"""
        reg = slots.build_registry(
            question="占比是多少",
            columns=["share_pct"],
            rows=[[25.0]],
            stats={"rows": 1},
        )
        assert reg.resolve("cell.0.0") == "25"
        assert (
            slots.scalar_answer(column="share_pct", value=25.0, question="占比是多少")
            == "share_pct 为 25。"
        )
        multi = slots.build_registry(
            question="q",
            columns=["share_pct"],
            rows=[[25.0], [10.0]],
            stats={
                "rows": 2,
                "share_pct_max": 25.0,
                "share_pct_min": 10.0,
                "share_pct_avg": 17.5,
            },
        )
        assert multi.resolve("argmax.share_pct.value") == "25"

    def test_scoped_registry_composes_with_calc(self) -> None:
        columns, rows, stats = _rating_case()
        scoped = slots.build_registry(
            question="q", columns=columns, rows=rows, stats=stats, scope="r0"
        )
        combined = slots.ScopedSlotRegistry(
            [scoped, slots.build_calc_registry({"delta_pct": 25.0})]
        )
        out = slots.apply_contract("{{r0.argmax.count.value}} 与 {{calc.delta_pct}}", combined)
        assert out.violation is None
        assert out.text == "223 与 25%"


class TestContract:
    def _registry(self) -> slots.SlotRegistry:
        columns, rows, stats = _rating_case()
        return slots.build_registry(
            question="统计每个评级的影片数量", columns=columns, rows=rows, stats=stats
        )

    def test_valid_text_renders(self) -> None:
        reg = self._registry()
        out = slots.apply_contract(
            "{{argmax.count.label}} 数量最多，共 {{argmax.count.value}} 部。", reg
        )
        assert out.violation is None
        assert out.text == "PG-13 数量最多，共 223 部。"

    def test_literal_digit_rejected(self) -> None:
        reg = self._registry()
        out = slots.apply_contract("影片评级中 PG-13 数量最多，G 数量最少。", reg)
        assert out.violation == "literal_digit"

    def test_fullwidth_digit_rejected(self) -> None:
        reg = self._registry()
        out = slots.apply_contract("共有２２３部。", reg)
        assert out.violation == "literal_digit"

    def test_malformed_braces_rejected(self) -> None:
        reg = self._registry()
        out = slots.apply_contract("共 {{rows} 行。", reg)
        assert out.violation == "malformed"

    def test_unknown_slot_rejected(self) -> None:
        reg = self._registry()
        out = slots.apply_contract("共 {{rows2}} 行。", reg)
        assert out.violation == "unknown_slot"
        assert out.detail == "rows2"

    def test_citation_marker_exempt_only_in_fused_mode(self) -> None:
        reg = self._registry()
        out = slots.apply_contract("口径见【员工手册-A7F2】。", reg, doc_parts=["口径说明"])
        assert out.violation is None
        assert out.text == "口径见【员工手册-A7F2】。"
        data = slots.apply_contract("口径见【员工手册-A7F2】。", reg)
        assert data.violation == "literal_digit"

    def test_doc_numbers_must_be_verbatim(self) -> None:
        reg = self._registry()
        parts = ["默认上限为 110 个 Pod,详见 2005-07-01 的说明。"]
        ok = slots.apply_contract("默认上限为 110 个 Pod。", reg, doc_parts=parts)
        assert ok.violation is None
        bad = slots.apply_contract("默认上限为 120 个 Pod。", reg, doc_parts=parts)
        assert bad.violation == "doc_number_unquoted"
        assert bad.detail == "120"

    def test_doc_path_still_rejects_unknown_slot(self) -> None:
        reg = self._registry()
        out = slots.apply_contract("{{nope.x}} 共 110 个。", reg, doc_parts=["上限 110 个"])
        assert out.violation == "unknown_slot"


class TestDeterministicAnswers:
    def test_scalar_count_with_measure(self) -> None:
        text = slots.scalar_answer(
            column="count",
            value=0,
            question="单笔支付金额超过100美元的订单一共有多少条？",
            aggregate="count",
        )
        assert text == "共 0 条。"

    def test_scalar_sum_with_measure(self) -> None:
        text = slots.scalar_answer(
            column="total_amount",
            value=14370.35,
            question="总金额是多少元",
            aggregate="sum",
        )
        assert text == "合计为 14370.35 元。"

    def test_scalar_without_measure(self) -> None:
        assert (
            slots.scalar_answer(column="count", value=7, question="有多少", aggregate="count")
            == "共 7。"
        )

    def test_scalar_phrases_come_from_aggregate_shape(self) -> None:
        assert slots.scalar_answer(column="x", value=30, question="总库存", aggregate="sum") == (
            "合计为 30。"
        )
        assert slots.scalar_answer(
            column="x", value=30, question="平均是多少", aggregate="avg"
        ) == ("平均为 30。")
        assert slots.scalar_answer(
            column="x", value=30, question="最高是多少", aggregate="max"
        ) == ("最高为 30。")
        assert slots.scalar_answer(
            column="x", value=30, question="最低是多少", aggregate="min"
        ) == ("最低为 30。")
        assert slots.scalar_answer(column="?column?", value=1, question="查一下") == "结果为 1。"

    def test_scalar_does_not_infer_from_column_name(self) -> None:
        # 列名叫 count 但 SQL 是 SUM:按 SQL 结构作答
        assert (
            slots.scalar_answer(column="count", value=30, question="总库存", aggregate="sum")
            == "合计为 30。"
        )
        # 列名叫 total 但 SQL 是 COUNT:不得按名字当总量
        assert (
            slots.scalar_answer(column="total", value=30, question="有多少", aggregate="count")
            == "共 30。"
        )
        # 无 SQL 结构证据:列名只作标签,不推断计数/合计
        assert slots.scalar_answer(column="count", value=30, question="查一下") == "count 为 30。"
        assert (
            slots.scalar_answer(column="discount_amount", value=8.5, question="平均折扣")
            == "discount_amount 为 8.5。"
        )
        assert (
            slots.scalar_answer(column="account_balance", value=120, question="余额")
            == "account_balance 为 120。"
        )
        assert (
            slots.scalar_answer(
                column="count_distinct_user",
                value=3,
                question="有多少用户",
                aggregate="count",
            )
            == "共 3。"
        )

    def test_scalar_unit_falls_back_to_original_question(self) -> None:
        # 改写问句丢了量词时,用用户原问句的量词;仍不读数据表
        assert (
            slots.scalar_answer(
                column="count",
                value=1000,
                question="影片总数是多少",
                unit_question="一共有多少部影片?",
                aggregate="count",
            )
            == "共 1000 部。"
        )
        assert (
            slots.scalar_answer(
                column="count",
                value=200,
                question="演员总数",
                unit_question="一共有多少位演员?",
                aggregate="count",
            )
            == "共 200 位。"
        )

    def test_scalar_unit_ignores_threshold_currency(self) -> None:
        # 阈值里的「100 美元」不是答案量词;问句没有「多少/几+量词」时不补单位
        assert (
            slots.scalar_answer(
                column="count",
                value=5,
                question="统计支付金额超过100美元的客户数量",
                aggregate="count",
            )
            == "共 5。"
        )

    def test_scalar_unit_original_question_wins_over_rewrite(self) -> None:
        # 改写句残留「单笔」不能被当成客户量词;原问句的「多少位」优先
        assert (
            slots.scalar_answer(
                column="count",
                value=0,
                question="单笔支付金额超过 100 美元的客户数",
                unit_question="单笔支付金额超过 100 美元的客户有多少位？",
                aggregate="count",
            )
            == "共 0 位。"
        )

    def test_outline_with_labels(self) -> None:
        columns, rows, stats = _rating_case()
        text = slots.outline_answer(columns=columns, rows=rows, stats=stats)
        assert text == "共 5 行；rating 为 PG-13 的 count 最高（223），G 最低（178）。"
        assert "最大值" not in text
        assert "未生成解读" not in text

    def test_outline_without_metric(self) -> None:
        text = slots.outline_answer(columns=["name"], rows=[["a"], ["b"]], stats={"rows": 2})
        assert text == "共 2 行。"

    def test_outline_without_metric_lists_focus_values(self) -> None:
        text = slots.outline_answer(
            columns=["city", "customer_count"],
            rows=[["Aurora", 2], ["London", 2]],
            stats={"rows": 2},
            label_index=0,
        )
        assert text == "共 2 行；city 包括 Aurora、London。"

    def test_scoped_calc_slot_keeps_percent_unit(self) -> None:
        reg = slots.build_calc_registry({"delta_pct.c2": -10.0})
        assert reg.resolve("calc.delta_pct.c2") == "-10%"

    def test_focus_label_prefers_question_dimension(self) -> None:
        columns = ["first_name", "last_name", "city", "total_payment_amount"]
        rows: list[list[object]] = [
            ["KARL", "SEAL", "Cape Coral", 221.55],
            ["RHONDA", "KENNEDY", "Apeldoorn", 194.61],
        ]
        labels = {"first_name": "名", "last_name": "姓", "city": "城市名称"}
        assert slots.focus_label_index(columns, rows, "这 5 位分别来自哪个城市？", labels) == 2
        assert slots.focus_label_index(columns, rows, "总支付金额最高的客户是谁？", labels) is None
        assert (
            slots.focus_label_index(
                ["status", "amount"],
                [["paid", 100], ["pending", 50]],
                "统计每个订单的金额",
                {"status": "订单状态"},
            )
            is None
        )
        assert slots.summary_mentions_label("最高的是 Cape Coral", columns, rows, 2, labels)
        assert not slots.summary_mentions_label("最高为 221.55", columns, rows, 2, labels)

    def test_argmax_label_uses_focus_dimension(self) -> None:
        columns = ["first_name", "last_name", "city", "total_payment_amount"]
        rows: list[list[object]] = [
            ["KARL", "SEAL", "Cape Coral", 221.55],
            ["RHONDA", "KENNEDY", "Apeldoorn", 194.61],
        ]
        stats = {"rows": 2, "total_payment_amount_max": 221.55, "total_payment_amount_min": 194.61}
        reg = slots.build_registry(
            question="这 5 位分别来自哪个城市？",
            columns=columns,
            rows=rows,
            stats=stats,
            column_labels={"first_name": "名", "last_name": "姓", "city": "城市名称"},
            label_question="这 5 位分别来自哪个城市？",
        )
        assert reg.resolve("argmax.total_payment_amount.label") == "Cape Coral"
        assert reg.resolve("argmin.total_payment_amount.label") == "Apeldoorn"
        text = slots.outline_answer(columns=columns, rows=rows, stats=stats, label_index=2)
        assert "city 包括 Cape Coral、Apeldoorn" in text
        assert "其中 Cape Coral 的 total_payment_amount 最高" in text


class TestHistoryShapes:
    """历史三类兜底形态(前提阈值/含数字标签/月度序列)在新契约下均可成句。"""

    def test_month_series_renders(self) -> None:
        columns = ["store_id", "month", "rental_count"]
        rows: list[list[object]] = [
            [1, datetime(2005, 7, 1, tzinfo=UTC), 3346],
            [2, datetime(2005, 8, 1, tzinfo=UTC), 2852],
        ]
        stats = {
            "rows": 2,
            "store_id_max": 2,
            "store_id_min": 1,
            "rental_count_max": 3346,
            "rental_count_min": 2852,
        }
        reg = slots.build_registry(
            question="按照租赁发生的月份，统计每家门店的租赁笔数",
            columns=columns,
            rows=rows,
            stats=stats,
        )
        out = slots.apply_contract(
            "{{cell.0.1}} 所在月份租赁笔数最高，共 {{cell.0.2}} 笔。",
            reg,
        )
        assert out.violation is None
        assert out.text == "2005-07-01 所在月份租赁笔数最高，共 3346 笔。"

    def test_premise_percent_keeps_unit(self) -> None:
        reg = slots.build_registry(
            question="统计增长率超过10%的月份",
            columns=["count"],
            rows=[[1]],
            stats={"rows": 1, "count_max": 1, "count_min": 1, "count_avg": 1.0},
        )
        out = slots.apply_contract("增长率超过 {{q.0}} 的月份共 {{cell.0.0}} 个。", reg)
        assert out.violation is None
        assert out.text == "增长率超过 10% 的月份共 1 个。"

    def test_premise_threshold_renders(self) -> None:
        reg = slots.build_registry(
            question="单笔支付金额超过100美元的订单一共有多少条",
            columns=["count"],
            rows=[[0]],
            stats={"rows": 1, "count_max": 0, "count_min": 0, "count_avg": 0.0},
        )
        out = slots.apply_contract("超过 {{q.0}} 美元的订单共 {{cell.0.0}} 条。", reg)
        assert out.violation is None
        assert out.text == "超过 100 美元的订单共 0 条。"


class TestSchemaAliases:
    def test_parse_header_lines(self) -> None:
        hint = "\n".join(
            [
                "public.payment(payment)(payment_id, amount) -- 支付流水",
                "public.film(film_id, title) -- 影片表",
                "表 public.legacy:内省回退形态",
            ]
        )
        assert slots.parse_schema_aliases(hint) == [("payment", "public.payment")]
