"""控制面运行时单例装配(路由与域集成共用)。"""

from kongming.config import get_settings
from kongming.control.runtime import ControlRuntime, build_runtime
from kongming.db import get_sessionmaker
from kongming.redis_client import get_redis

_runtime: ControlRuntime | None = None


def get_control_runtime() -> ControlRuntime:
    """进程级单例:配置变更需重启(与其余组合根同口径)。"""
    global _runtime
    if _runtime is None:
        _runtime = build_runtime(
            settings=get_settings(),
            maker=get_sessionmaker(),
            redis=get_redis(),
        )
    return _runtime


def install_control_runtime(runtime: ControlRuntime | None) -> None:
    """测试/演练替换运行时(None=下次按配置重建)。"""
    global _runtime
    _runtime = runtime


def control_runtime_optional() -> ControlRuntime | None:
    return _runtime
