# pyright: reportPrivateUsage=false
"""节点阶段轨迹收集器用例(spec「节点阶段轨迹持久化」):
成对事件/重试序号/相对耗时/上限截断/畸形帧跳过/零事件 None。"""

from kongming.chat.stage_trace import StageTraceCollector


class _FakeClock:
    """毫秒步进假时钟(无 sleep)。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.001  # 每次读取 +1ms
        return self.now


def _collector(max_events: int = 64) -> tuple[StageTraceCollector, _FakeClock]:
    clock = _FakeClock()
    return StageTraceCollector(max_events=max_events, clock=clock), clock


def test_paired_events_with_retry_and_offsets() -> None:
    c, _ = _collector()
    c.add_stage({"node": "understand", "status": "running", "retry_count": 0})
    c.add_stage({"node": "understand", "status": "done", "retry_count": 0})
    c.add_stage({"node": "validate", "status": "done", "retry_count": 1})
    payload = c.payload()
    assert payload is not None and len(payload) == 3
    assert payload[0] == {"node": "understand", "status": "running", "retry": 0, "t": 1}
    assert payload[2]["retry"] == 1
    ts = [int(e["t"]) for e in payload]  # type: ignore[arg-type]
    assert ts == sorted(ts)  # 相对耗时单调


def test_empty_stream_yields_none() -> None:
    c, _ = _collector()
    assert c.payload() is None  # 同步轮/无帧流:如实为空


def test_malformed_frames_skipped() -> None:
    c, _ = _collector()
    c.add_stage({"status": "running"})  # 缺 node
    c.add_stage({"node": "", "status": "running"})  # 空 node
    c.add_stage({"node": "x", "status": "bogus"})  # 非法 status
    c.add_stage("not-a-dict")  # type: ignore[arg-type]
    assert c.payload() is None


def test_cap_and_truncation_marker() -> None:
    c, _ = _collector(max_events=4)
    for i in range(10):
        c.add_stage({"node": f"n{i}", "status": "done", "retry_count": 0})
    payload = c.payload()
    assert payload is not None and len(payload) == 5  # 4 条 + 截断标记
    assert payload[-1] == {"truncated": True}
    assert all("node" in e for e in payload[:4])  # type: ignore[arg-type]
