"""stt 域端口(114):引擎接口反转——领域零框架依赖。

WhisperEngine 由组合根装配真实实现(faster-whisper 懒加载);测试注入
mock。转写产物是问题文本(非附件内容),即转即弃,零持久化。
"""

from typing import Protocol


class WhisperEngine(Protocol):
    """本地语音转写引擎契约。实现负责模型加载/解码/推理全部细节。"""

    async def transcribe(self, audio: bytes) -> str:
        """音频字节 → 转写文本(空音频/纯静音返回空串,不抛错)。"""
        ...


class EngineUnavailable(Exception):
    """引擎不可用(依赖缺失/模型未下载/加载失败)——端点映射 503 明示配方。"""
