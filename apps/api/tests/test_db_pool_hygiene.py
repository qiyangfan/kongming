"""074 连接池弹性:配置缺省零变化 + pre_ping/容量/换代可配。"""

from typing import Any, cast

from kongming.config import Settings, get_settings
from kongming.db import get_engine


def test_pool_settings_defaults_match_history() -> None:
    # 字段缺省值即历史行为(构造面不读 env 也能断言)
    assert Settings.model_fields["db_pool_size"].default == 5
    assert Settings.model_fields["db_max_overflow"].default == 5
    assert Settings.model_fields["db_pool_recycle_sec"].default == 0


def test_engine_uses_settings_and_pre_ping(monkeypatch: Any) -> None:
    monkeypatch.setenv("KM_DB_POOL_SIZE", "8")
    monkeypatch.setenv("KM_DB_MAX_OVERFLOW", "3")
    monkeypatch.setenv("KM_DB_POOL_RECYCLE_SEC", "120")
    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        eng = get_engine()
        pool = cast("Any", eng.pool)
        assert pool.size() == 8
        assert pool._max_overflow == 3
        assert pool._recycle == 120
        assert pool._pre_ping is True
    finally:
        get_settings.cache_clear()
        get_engine.cache_clear()
