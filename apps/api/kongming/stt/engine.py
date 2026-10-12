"""stt 引擎(114):faster-whisper 懒加载单例——组合根装配的真实实现。

加载策略(design D2):进程内首次请求时加载并缓存(asyncio.Lock 防并发
双载;加载是阻塞调用走 to_thread),失败收敛 EngineUnavailable(端点 503
明示配方)。模型文件缺失时 faster-whisper 会尝试从 HuggingFace 下载——
离线环境即加载失败 503,预下载配方见 docs/HANDOFF.md。
"""

import asyncio
import io
import logging
from typing import Any

from kongming.stt.ports import EngineUnavailable

log = logging.getLogger(__name__)


def _decode_audio_16k(data: bytes) -> Any:
    """音频 bytes → 16kHz mono float32 numpy(av 解码;webm/opus/mp3/wav/m4a
    通吃)。不用 faster_whisper.audio.decode_audio:其内部 av.open 传的
    metadata_errors 参数在 av>=15 已移除(本仓 av 15 wheel 覆盖 py3.13)。"""
    import av
    import numpy as np

    container: Any = av.open(io.BytesIO(data))  # av stubs 对流式输入分派不全,显式 Any
    audio_stream = next(s for s in container.streams if s.type == "audio")
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks: list[Any] = []
    for frame in container.decode(audio_stream):
        for rf in resampler.resample(frame):
            chunks.append(rf.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    joined = np.concatenate(chunks)
    return joined.astype(np.float32) / 32768.0


class FasterWhisperEngine:
    """WhisperEngine 的真实实现。audio bytes → PyAV 解码 → 模型转写。"""

    def __init__(self, model_size: str = "small") -> None:
        self._model_size = model_size
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-untyped]

                log.info("加载语音模型 %s(int8/cpu)——首次可能需要下载", self._model_size)
                self._model = await asyncio.to_thread(
                    WhisperModel, self._model_size, device="cpu", compute_type="int8"
                )
            except ImportError as e:
                raise EngineUnavailable("faster-whisper 未安装(uv sync 后可用)") from e
            except Exception as e:  # noqa: BLE001  # 模型缺失/下载失败/加载错误统一明示
                raise EngineUnavailable(
                    f"语音模型不可用({self._model_size}):{e}——预下载配方见 docs/HANDOFF.md"
                ) from e
            return self._model

    async def transcribe(self, audio: bytes) -> str:
        model = await self._ensure_model()
        try:
            decoded = await asyncio.to_thread(_decode_audio_16k, audio)

            def _run() -> str:
                # 创建+消费同线程:faster-whisper 的 transcribe 返回 lazy
                # generator,真正的 decoder 推理在逐段迭代时发生——消费若留在
                # 事件循环线程会阻塞全进程(评审修复:原实现只把创建放进
                # to_thread,seg 迭代仍在循环线程跑)
                segments, _info = model.transcribe(decoded, language="zh", beam_size=5)
                return "".join(seg.text for seg in segments)

            return await asyncio.to_thread(_run)
        except EngineUnavailable:
            raise
        except Exception as e:  # noqa: BLE001  # 解码/推理错误由 service 收敛为 503
            raise RuntimeError(f"转写推理失败: {e}") from e
