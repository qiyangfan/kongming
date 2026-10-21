"""SSE 帧解析纯函数(query-streaming)。

控制面中继消费 agent 面 /query/stream 的 text/event-stream 字节流:
逐行喂入解码器,空行为帧边界。契约见 query-streaming spec「SSE 事件协议」
——注释行(`:` 开头,心跳)忽略;`event:`/`data:` 行组装;无 event 字段的
帧 event 返回空串(消费方按未知事件忽略);多 data 行以 \\n 拼接(SSE 规范)。
"""

from dataclasses import dataclass, field


@dataclass
class SseDecoder:
    """增量 SSE 解码器:feed(行) → 完整帧 (event, data) 或 None(未成帧)。"""

    _event: str = ""
    _data_lines: list[str] = field(default_factory=list[str])

    def feed(self, line: str) -> tuple[str, str] | None:
        """喂入一行(不含行尾符);帧边界(空行)返回完整帧并复位累积。"""
        if line == "":
            if not self._event and not self._data_lines:
                return None  # 连续空行/流开始的空帧:跳过
            frame = (self._event, "\n".join(self._data_lines))
            self._event = ""
            self._data_lines = []
            return frame
        if line.startswith(":"):
            return None  # 注释行(心跳):按契约忽略
        if line.startswith("event:"):
            self._event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            # SSE 规范:data: 后仅剥一个前导空格
            payload = line[len("data:") :]
            if payload.startswith(" "):
                payload = payload[1:]
            self._data_lines.append(payload)
        return None  # 其余字段(id:/retry:)不消费
