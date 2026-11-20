"""Agent 测试基建:env 先置(任何 kongming_agent 导入前),防本地 .env 渗入测试面。"""

import os
import socket
from typing import Any

import pytest

os.environ.setdefault("KM_AGENT_INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("KM_AGENT_LLM_API_KEY", "")
os.environ.setdefault("KM_AGENT_API_BASE_URL", "http://api-unused-in-tests")
# 多模型注册表/路由(真值经 .env 渗入会污染路由断言,同款)
os.environ.setdefault("KM_AGENT_MODEL_REGISTRY_JSON", "")
os.environ.setdefault("KM_AGENT_MODEL_ROUTES_JSON", "")


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试不得发起外网连接:非回环 TCP 目标直接失败,外呼必须显式 mock。"""
    real_connect = socket.socket.connect

    def guarded(self: socket.socket, address: tuple[Any, ...] | str | bytes) -> None:
        host: object = address[0] if isinstance(address, tuple) else address
        # 本机回环与 unix socket(路径形态)放行,其余目标一律视为越界外呼
        if (
            isinstance(host, str)
            and host not in {"127.0.0.1", "::1", "localhost"}
            and not host.startswith("/")
        ):
            raise OSError(f"测试禁止外网连接: {host}")
        real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
