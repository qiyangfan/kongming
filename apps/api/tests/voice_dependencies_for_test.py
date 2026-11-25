"""测试专用:把组合根的 voice 服务替换为脚本化替身(覆盖依赖注入)。"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from kongming.chat.dependencies import get_voice_service
from kongming.voice.service import VoiceService, VoiceSettingsView
from tests.test_voice_calls import FakeGate, FakeGateway, FakeGuard, FakeTickets


@contextmanager
def install_voice_service(
    app: Any,
    *,
    enabled: bool,
    answer: bytes = b"v=0\r\nok\r\n",
    tickets: Any = None,
    max_call_seconds: int = 600,
    operlog: Any = None,
    guard: Any = None,
) -> Generator[VoiceService]:
    svc = VoiceService(
        FakeGate(),
        FakeGateway(answer),
        tickets if tickets is not None else FakeTickets(),
        guard=guard if guard is not None else FakeGuard(),
        operlog=operlog,
        settings=VoiceSettingsView(
            voice_api_key="k" if enabled else None,
            voice_realtime_base_url="https://rt.example" if enabled else None,
            voice_model="m" if enabled else None,
            voice_max_call_seconds=max_call_seconds,
        ),
    )
    # override 键是依赖函数本身(Depends(get_voice_service))
    app.dependency_overrides[get_voice_service] = lambda: svc
    try:
        yield svc
    finally:
        app.dependency_overrides.pop(get_voice_service, None)
