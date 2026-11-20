"""ADR-002:agent 面无数据库/Redis 凭证,镜像与编排层同样不得注入。"""

import re
from pathlib import Path
from typing import Any, cast

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_DB_KNOB = re.compile(r"KM_DATABASE_URL|KM_DB_|KM_REDIS|POSTGRES")


def _compose() -> dict[str, Any]:
    """读取编排文件;compose 结构固定,类型仅为静态检查收口。"""
    loaded: object = yaml.safe_load(
        (_ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")
    )
    assert isinstance(loaded, dict)
    return cast("dict[str, Any]", loaded)


def _agent_env() -> dict[str, Any]:
    """agent 服务的 environment 映射(缺失时按空映射处理)。"""
    services = cast("dict[str, Any]", _compose()["services"])
    agent = cast("dict[str, Any]", services["agent"])
    return cast("dict[str, Any]", agent.get("environment") or {})


def test_agent_service_env_has_no_db_or_redis_credentials() -> None:
    """agent 服务的环境变量里出现任何数据库/Redis 键即违反信任边界。"""
    assert [key for key in _agent_env() if _DB_KNOB.search(key)] == []


def test_agent_dockerfile_declares_no_db_or_redis_env() -> None:
    """镜像层同样不得声明数据库/Redis 环境变量(构造级保证)。"""
    text = (_ROOT / "infra/agent.Dockerfile").read_text(encoding="utf-8")
    declares = [line for line in text.splitlines() if line.strip().startswith(("ENV", "ARG"))]
    assert [line for line in declares if _DB_KNOB.search(line)] == []
