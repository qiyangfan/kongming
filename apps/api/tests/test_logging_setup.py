"""061:运行日志配置——INFO 不丢弃、级别可配。"""

import logging
from typing import Any

from kongming.logging_setup import setup_logging


def test_info_not_dropped_by_default(capsys: Any) -> None:
    setup_logging(level="INFO")
    logging.getLogger("km.test").info("hello-061-info")
    err = capsys.readouterr().err
    assert "hello-061-info" in err


def test_level_configurable(capsys: Any) -> None:
    setup_logging(level="WARNING")
    logging.getLogger("km.test").info("should-not-appear")
    logging.getLogger("km.test").warning("should-appear")
    err = capsys.readouterr().err
    assert "should-not-appear" not in err
    assert "should-appear" in err
