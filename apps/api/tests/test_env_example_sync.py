"""`.env.example` 与 Settings 字段漂移守卫。

模板缺项意味着新部署无从发现该旋钮(Settings extra="ignore",拼错/漏写不会报错);
反向检查防止 api 侧改名后模板残留旧键名。
"""

import re
from pathlib import Path

from kongming.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"
COMPOSE = Path(__file__).resolve().parents[3] / "infra/docker-compose.yml"

# compose 注入或内部装配名,不面向运维手配
INTERNAL_ONLY = {
    "KM_DATABASE_URL",
    "KM_REDIS_URL",
    "KM_AGENT_BASE_URL",
    "KM_APP_NAME",
}

# 非 Settings 字段的合法模板项(纯 compose 变量,由编排消费)
EXTERNAL_OK = {
    "KM_DB_PASSWORD",
    "KM_API_HOST_PORT",
    "KM_AGENT_HOST_PORT",
    "KM_REDIS_HOST_PORT",
}

_ASSIGNMENT = re.compile(r"^\s*#?\s*(KM_[A-Z0-9_]+)=", re.M)
_COMPOSE_REF = re.compile(r"\$\{(KM_[A-Z0-9_]+)")


def _documented_keys() -> set[str]:
    """收集模板中已登记(含注释形式)的 KM_ 变量名。"""
    return set(_ASSIGNMENT.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def _settings_keys() -> set[str]:
    """Settings 字段对应的环境变量名(env_prefix=KM_)。"""
    return {f"KM_{name.upper()}" for name in Settings.model_fields}


def test_every_setting_field_is_documented() -> None:
    missing = _settings_keys() - _documented_keys() - INTERNAL_ONLY
    assert not missing, f".env.example 缺少以下配置项说明: {sorted(missing)}"


def test_documented_keys_belong_to_known_config() -> None:
    # agent 面配置走 KM_AGENT_ 前缀独立 Settings;api 侧改名后模板键应同步消失
    unknown = {
        key
        for key in _documented_keys()
        if key not in _settings_keys()
        and key not in EXTERNAL_OK
        and not key.startswith("KM_AGENT_")
    }
    assert not unknown, f".env.example 含无主配置项(疑似旧名残留): {sorted(unknown)}"


def test_compose_variables_are_documented() -> None:
    """compose 引用的每个 KM_ 变量都必须在模板登记,避免编排侧出现暗藏开关。"""
    referenced = set(_COMPOSE_REF.findall(COMPOSE.read_text(encoding="utf-8")))
    missing = referenced - _documented_keys()
    assert not missing, f".env.example 缺少 compose 引用的变量: {sorted(missing)}"
