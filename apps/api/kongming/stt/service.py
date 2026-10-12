"""stt 域用例(114):转写编排——限制校验→引擎→文本。

零持久化(即转即弃):音频 bytes 不落盘、不建附件行;转写文本不进日志
(操作审计仅记 action)。失败面收敛为两类:超限(413)与引擎不可用
(503),由 router 映射 HTTP。
"""

import logging

from kongming.stt.ports import EngineUnavailable, WhisperEngine

log = logging.getLogger(__name__)

_MAX_AUDIO_BYTES = 15 * 1024 * 1024  # 15MB(design D4;浏览器 MediaRecorder 1min webm 远小于此)


class AudioTooLarge(Exception):
    """音频超 15MB 上限——router 映射 413。"""


class SttService:
    def __init__(self, engine: WhisperEngine) -> None:
        self._engine = engine

    async def transcribe(self, audio: bytes) -> str:
        if len(audio) == 0:
            raise EngineUnavailable("空音频")
        if len(audio) > _MAX_AUDIO_BYTES:
            raise AudioTooLarge(f"音频超 {_MAX_AUDIO_BYTES // 1024 // 1024}MB 上限")
        try:
            text = await self._engine.transcribe(audio)
        except EngineUnavailable:
            raise
        except Exception as e:  # noqa: BLE001  # 推理失败收敛为引擎不可用(503,配方指引)
            log.warning("语音转写失败", exc_info=True)
            raise EngineUnavailable(f"转写失败: {e}") from e
        return " ".join(text.split())  # 单行折叠(与建议问题防线同款)
