"""配额判定纯函数回归:阈值边界、超限维度、0=不限与成本整数厘。"""

from kongming.quota import check_quota, estimate_cost


def test_check_quota_matrix() -> None:
    assert check_quota(used=50, limit=100, warn_pct=80, dimension="TOKENS").allowed is True
    assert check_quota(used=80, limit=100, warn_pct=80, dimension="TOKENS").warn is True
    verdict = check_quota(used=100, limit=100, warn_pct=80, dimension="CALLS")
    assert verdict.allowed is False and verdict.dimension == "CALLS"
    unlimited = check_quota(used=999999, limit=0, warn_pct=80, dimension="COST")
    assert unlimited.allowed is True and unlimited.warn is False


def test_estimate_cost_int_fen() -> None:
    # 2500 token ×2厘/千 + 3 次 ×5厘 = 5+15=20 厘(向下取整)
    assert estimate_cost(tokens=2500, calls=3, price_per_mtok=2, price_per_call=5) == 20
    assert estimate_cost(tokens=0, calls=0, price_per_mtok=0, price_per_call=0) == 0
