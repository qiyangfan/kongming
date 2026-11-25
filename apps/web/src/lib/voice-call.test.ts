// 通话纯函数:事件分流、状态归约、通道地址(通话 hook 与页面的可测内核)。

import { describe, expect, it } from "vitest";
import {
  allowPlayback,
  classifyEstablishFailure,
  classifyModelEvent,
  channelUrl,
  computeAudioLevel,
  INITIAL_PLAYBACK_GATE,
  reduceCallPhase,
  reducePlayback,
  smoothAudioLevel,
  suppressActivePlayback,
  type CallPhase,
  type PlaybackGate,
  type PlaybackSignal,
} from "./voice-call";

describe("classifyModelEvent 事件分流", () => {
  it("说话开始也必须原样转发:后端据此取消正在播报的回应", () => {
    expect(classifyModelEvent({ type: "input_audio_buffer.speech_started" })).toBe("relay");
  });

  it("回应结束原样转给后端(用量累计);播放控制由页面按回应结束自行处理", () => {
    // response.done 也要原样转给后端(用量累计);播放控制由页面按回应结束自行处理
    expect(classifyModelEvent({ type: "response.done" })).toBe("relay");
  });

  it("其余模型事件一律原样转给后端(浏览器只搬运)", () => {
    for (const type of [
      "conversation.item.input_transcription.completed",
      "response.created",
      "session.updated",
      "error",
    ]) {
      expect(classifyModelEvent({ type })).toBe("relay");
    }
  });

  it("没有 type 的载荷不转发(畸形帧丢弃)", () => {
    expect(classifyModelEvent({})).toBe("drop");
    expect(classifyModelEvent("not-an-object")).toBe("drop"); // 非对象:直接丢弃
  });
});

function feed(
  gate: PlaybackGate,
  events: readonly unknown[],
): { gate: PlaybackGate; signals: (PlaybackSignal | null)[] } {
  const signals: (PlaybackSignal | null)[] = [];
  for (const event of events) {
    const step = reducePlayback(gate, event);
    gate = step.gate;
    signals.push(step.signal);
  }
  return { gate, signals };
}

describe("reducePlayback 播放闸门", () => {
  it("未放行时音频增量不解除静音;放行后才出声", () => {
    const created = feed(INITIAL_PLAYBACK_GATE, [
      { type: "response.created", response: { id: "r1" } },
    ]);
    const muted = feed(created.gate, [{ type: "response.audio.delta", response_id: "r1" }]);
    expect(muted.signals).toEqual(["hold"]);

    const allowed = feed(allowPlayback(muted.gate), [
      { type: "response.audio.delta", response_id: "r1" },
    ]);
    expect(allowed.signals).toEqual(["unmute"]);
  });

  it("插话只压住被打断的回应;其残余音频不再解除静音", () => {
    const opened = feed(allowPlayback(INITIAL_PLAYBACK_GATE), [
      { type: "response.created", response: { id: "r1" } },
      { type: "response.audio.delta", response_id: "r1" },
      { type: "input_audio_buffer.speech_started" },
    ]);
    expect(opened.signals).toEqual([null, "unmute", "mute"]);
    expect(opened.gate.suppressed.has("r1")).toBe(true);
    expect(opened.gate.allowed).toBe(false);

    const resumed = feed(opened.gate, [
      { type: "response.audio.delta", response_id: "r1" },
      { type: "response.done", response: { id: "r1" } },
      { type: "response.audio.delta", response_id: "r2" },
    ]);
    expect(resumed.signals).toEqual(["hold", "finished", "hold"]);
    expect(resumed.gate.suppressed.size).toBe(0);
  });

  it("新问到达时撤销放行并压住当前回应;新回答放行后不受旧回应影响", () => {
    const created = feed(allowPlayback(INITIAL_PLAYBACK_GATE), [
      { type: "response.created", response: { id: "old-answer" } },
    ]);
    const suppressed = suppressActivePlayback(created.gate);
    expect(suppressed.suppressed.has("old-answer")).toBe(true);
    expect(suppressed.allowed).toBe(false);

    const step = feed(allowPlayback(suppressed), [
      { type: "response.audio.delta", response_id: "old-answer" },
      { type: "response.created", response: { id: "new-answer" } },
      { type: "response.audio.delta", response_id: "new-answer" },
    ]);
    expect(step.signals).toEqual(["hold", null, "unmute"]);
  });

  it("还没拿到回应 id 时插话:兜底压制到下一个回应开始;放行后新回应照常出声", () => {
    const interrupted = feed(INITIAL_PLAYBACK_GATE, [
      { type: "input_audio_buffer.speech_started" },
    ]);
    expect(interrupted.signals).toEqual(["mute"]);
    expect(interrupted.gate.suppressAll).toBe(true);
    expect(interrupted.gate.allowed).toBe(false);

    const resumed = feed(allowPlayback(interrupted.gate), [
      { type: "response.created", response: { id: "r1" } },
      { type: "response.audio.delta", response_id: "r1" },
    ]);
    expect(resumed.signals).toEqual([null, "unmute"]);
    expect(resumed.gate.suppressAll).toBe(false);
  });

  it("回应结束后放行失效:下一条音频必须再次收到放行", () => {
    const allowed = feed(allowPlayback(INITIAL_PLAYBACK_GATE), [
      { type: "response.created", response: { id: "r1" } },
      { type: "response.audio.delta", response_id: "r1" },
      { type: "response.done", response: { id: "r1" } },
      { type: "response.audio.delta", response_id: "r1" },
    ]);
    expect(allowed.signals).toEqual([null, "unmute", "finished", "hold"]);
    expect(allowed.gate.allowed).toBe(false);
  });

  it("旧回应的结束事件不打断新回应:不误报 finished、不撤销新回应的放行", () => {
    const opened = feed(allowPlayback(INITIAL_PLAYBACK_GATE), [
      { type: "response.created", response: { id: "r-old" } },
      { type: "response.created", response: { id: "r-new" } },
    ]);
    const ended = feed(opened.gate, [{ type: "response.done", response: { id: "r-old" } }]);
    expect(ended.signals).toEqual([null]);
    expect(ended.gate.active).toBe("r-new");
    expect(ended.gate.allowed).toBe(true);
    expect(
      feed(ended.gate, [{ type: "response.audio.delta", response_id: "r-new" }]).signals,
    ).toEqual(["unmute"]);
  });
});

describe("reduceCallPhase 通话状态归约", () => {
  it("idle → connecting → listening(established 且通道打开)", () => {
    let phase: CallPhase = reduceCallPhase("idle", { kind: "establish_started" });
    expect(phase).toBe<CallPhase>("connecting");
    phase = reduceCallPhase(phase, { kind: "channel_open" });
    expect(phase).toBe<CallPhase>("listening");
  });

  it("turn.request → querying;结果回填播报 → speaking;播完回 listening", () => {
    let phase: CallPhase = reduceCallPhase("listening", { kind: "turn_requested" });
    expect(phase).toBe("querying");
    phase = reduceCallPhase(phase, { kind: "playback_started" });
    expect(phase).toBe("speaking");
    phase = reduceCallPhase(phase, { kind: "playback_finished" });
    expect(phase).toBe("listening");
  });

  it("插话打断:播报中说话开始即静音并回 listening", () => {
    const phase = reduceCallPhase("speaking", { kind: "speech_started" });
    expect(phase).toBe("listening");
  });

  it("停止确认:查询中/播报中/聆听中都回到 listening", () => {
    for (const phase of ["querying", "speaking", "listening"] as CallPhase[]) {
      expect(reduceCallPhase(phase, { kind: "turn_stopped" })).toBe("listening");
    }
  });

  it("失败收尾:任何阶段遇 establish_failed / channel_closed → failed/idle", () => {
    expect(reduceCallPhase("connecting", { kind: "establish_failed" })).toBe("failed");
    expect(reduceCallPhase("listening", { kind: "channel_closed" })).toBe("idle");
    expect(reduceCallPhase("speaking", { kind: "channel_closed" })).toBe("idle");
  });

  it("挂断清理:任何阶段 hang_up → idle", () => {
    for (const phase of ["connecting", "listening", "querying", "speaking"] as CallPhase[]) {
      expect(reduceCallPhase(phase, { kind: "hang_up" })).toBe("idle");
    }
  });
});

describe("channelUrl 通道地址构造", () => {
  it("https 页面 → wss 同源路径", () => {
    expect(channelUrl("https://km.example")).toBe("wss://km.example/api/voice/calls/channel");
  });

  it("http 本地开发 → ws", () => {
    expect(channelUrl("http://localhost:5173")).toBe("ws://localhost:5173/api/voice/calls/channel");
  });

  it("票据不进 URL", () => {
    const url = channelUrl("http://localhost:5173");
    expect(url).not.toContain("ticket=");
    expect(url).not.toContain("?");
  });
});

describe("classifyEstablishFailure 建立失败分类", () => {
  it("麦克风被拒/无设备/被占用归为 mic", () => {
    expect(classifyEstablishFailure({ name: "NotAllowedError" })).toBe("mic");
    expect(classifyEstablishFailure({ name: "NotFoundError" })).toBe("mic");
    expect(classifyEstablishFailure({ name: "NotReadableError" })).toBe("mic");
  });

  it("后端 403 归为无权限,VOICE_NOT_ENABLED 归为未启用", () => {
    expect(classifyEstablishFailure({ status: 403 })).toBe("forbidden");
    expect(classifyEstablishFailure({ raw: "VOICE_NOT_ENABLED" })).toBe("not_enabled");
  });

  it("其余(含非对象错误)归为一般失败", () => {
    expect(classifyEstablishFailure({ status: 503, raw: "VOICE_UNAVAILABLE" })).toBe("network");
    expect(classifyEstablishFailure({ status: 502, raw: "VOICE_SIGNALING_FAILED" })).toBe(
      "network",
    );
    expect(classifyEstablishFailure("boom")).toBe("network");
    expect(classifyEstablishFailure(null)).toBe("network");
  });
});

describe("classifyEstablishFailure 通话冲突与占满", () => {
  it("后端 409 已有一通/并发已满各归其类(不再挤进一般失败)", () => {
    expect(classifyEstablishFailure({ status: 409, raw: "VOICE_CALL_EXISTS" })).toBe("exists");
    expect(classifyEstablishFailure({ status: 409, raw: "VOICE_BUSY" })).toBe("busy");
  });

  it("429 频控与日预算各归其类", () => {
    expect(classifyEstablishFailure({ status: 429, raw: "VOICE_RATE_LIMITED" })).toBe(
      "rate_limited",
    );
    expect(classifyEstablishFailure({ status: 429, raw: "VOICE_DAILY_LIMIT" })).toBe("daily_limit");
  });

  it("ICE 媒体连接失败归为网络受限(ice)", () => {
    expect(classifyEstablishFailure({ raw: "VOICE_ICE_FAILED" })).toBe("ice");
  });
});

describe("computeAudioLevel 与 smoothAudioLevel 音频音量采集", () => {
  it("空样本与静音(128)返回 0;底噪以下压为 0", () => {
    expect(computeAudioLevel([])).toBe(0);
    expect(computeAudioLevel(new Uint8Array(64).fill(128))).toBe(0);
    expect(computeAudioLevel(new Uint8Array(64).fill(129))).toBe(0);
  });

  it("说话信号映射到 (0, 1] 且峰值钳位 1", () => {
    const speech = new Uint8Array(64).map((_, i) => (i % 2 === 0 ? 150 : 106));
    const level = computeAudioLevel(speech);
    expect(level).toBeGreaterThan(0.3);
    expect(level).toBeLessThanOrEqual(1);

    const loud = new Uint8Array(64).map((_, i) => (i % 2 === 0 ? 255 : 0));
    expect(computeAudioLevel(loud)).toBe(1);
  });

  it("包络平滑:起音快于回落,近零归零", () => {
    const rising = smoothAudioLevel(0, 1);
    const falling = smoothAudioLevel(1, 0);
    expect(rising).toBeGreaterThan(0.3);
    expect(falling).toBeGreaterThan(0.7);
    expect(smoothAudioLevel(0.002, 0)).toBe(0);
  });
});
