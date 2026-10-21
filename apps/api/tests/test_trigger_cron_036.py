"""覆盖 cron 步进表达式、远期计算和日期边界。"""

import datetime as dt
import time

import pytest

from kongming.trigger.models import CronError, next_fire, parse_cron

_TZ = dt.timezone(dt.timedelta(hours=8))


def _at(y: int, mo: int, d: int, h: int, mi: int) -> dt.datetime:
    return dt.datetime(y, mo, d, h, mi, tzinfo=_TZ)


# ── a/b 形态（Vixie 兼容）──


def test_parse_step_from_value() -> None:
    """spec「a/b 形态兼容」:5/10=从 5 起步进 10 至段上限。"""
    assert parse_cron("5/10 * * * *")[0] == {5, 15, 25, 35, 45, 55}
    assert parse_cron("3/7 * * * *")[0] == {3, 10, 17, 24, 31, 38, 45, 52, 59}


def test_parse_step_forms_zero_regression() -> None:
    """原形态:*/n 与 a-b/n 不变。"""
    assert parse_cron("*/15 * * * *")[0] == {0, 15, 30, 45}
    assert parse_cron("1-10/2 * * * *")[0] == {1, 3, 5, 7, 9}


def test_parse_step_out_of_bounds_rejected() -> None:
    with pytest.raises(CronError):
        parse_cron("60/10 * * * *")


# ── 日历字段跳跃，远期表达式仍可快速计算 ──


def test_next_fire_far_future_instant() -> None:
    """spec「远未来表达式即时计算」:每年 1 月 1 日——毫秒级正确返回
    (原逐 tick 实现=52 万次迭代 0.3-0.5s 阻塞)。"""
    after = _at(2026, 8, 24, 10, 0)
    t0 = time.perf_counter()
    out = next_fire("0 0 1 1 *", after)
    elapsed = time.perf_counter() - t0
    assert out == _at(2027, 1, 1, 0, 0)
    assert elapsed < 1.0  # 日历跳跃:耗时与跨度无关(原实现 ~0.4s 边缘)


def test_next_fire_yearly_leap_day() -> None:
    """闰日:下个 2 月 29 日跨年命中(日级推进上界内)。"""
    out = next_fire("0 0 29 2 *", _at(2026, 3, 1, 0, 0))
    assert out == _at(2028, 2, 29, 0, 0)


def test_next_fire_impossible_day_raises() -> None:
    """永不日(2 月 31 日):上界内无解→CronError(不死循环)。"""
    with pytest.raises(CronError):
        next_fire("0 0 31 2 *", _at(2026, 1, 1, 0, 0))


# ── 月末/日常边界 ──


def test_next_fire_month_end() -> None:
    """每月 31 日:2 月跳过落 3 月 31 日。"""
    out = next_fire("0 0 31 * *", _at(2026, 2, 15, 0, 0))
    assert out == _at(2026, 3, 31, 0, 0)


def test_next_fire_daily_zero_regression() -> None:
    """每日 09:30:当天未到→当天,已过→次日(原语义逐字一致)。"""
    assert next_fire("30 9 * * *", _at(2026, 8, 24, 9, 29)) == _at(2026, 8, 24, 9, 30)
    assert next_fire("30 9 * * *", _at(2026, 8, 24, 9, 30)) == _at(2026, 8, 25, 9, 30)


def test_next_fire_weekly_dow() -> None:
    """每周一 09:00(dow 判定;2026-08-24 是周一)。"""
    assert next_fire("0 9 * * 1", _at(2026, 8, 24, 8, 0)) == _at(2026, 8, 24, 9, 0)
    assert next_fire("0 9 * * 1", _at(2026, 8, 24, 9, 0)) == _at(2026, 8, 31, 9, 0)


def test_next_fire_double_nonstar_still_rejected() -> None:
    """日/周双非 * 拒(既定立约)。"""
    with pytest.raises(CronError):
        parse_cron("0 0 1 * 1")
