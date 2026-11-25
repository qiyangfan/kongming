/* 语音通话纯函数:事件分流、状态归约、通道地址(通话 hook 与页面的可测内核)。 */

/** 通话页面状态:空闲/连接中/聆听/查询中/播报中/失败 */
export type CallPhase = "idle" | "connecting" | "listening" | "querying" | "speaking" | "failed";

/** 页面级通话事件(把模型事件与通道事件归一为有限集) */
export type CallEvent =
  | { kind: "establish_started" }
  | { kind: "establish_failed" }
  | { kind: "channel_open" }
  | { kind: "channel_closed" }
  | { kind: "turn_requested" }
  | { kind: "turn_stopped" }
  | { kind: "speech_started" }
  | { kind: "playback_started" }
  | { kind: "playback_finished" }
  | { kind: "hang_up" };

/** 模型会话事件的处置:原样转给后端 / 丢弃。本地静音由播放闸门独立处理,不改变转发。 */
type ModelEventAction = "relay" | "drop";

/** 浏览器只搬运;说话开始也必须转发,后端据此取消正在播报的回应。 */
export function classifyModelEvent(event: unknown): ModelEventAction {
  if (typeof event !== "object" || event === null) return "drop";
  const type = (event as { type?: unknown }).type;
  if (typeof type !== "string" || type.length === 0) return "drop";
  return "relay";
}

/** 会出声的模型事件(起播判断);字幕增量也算,保证固定应答尽早让位。 */
const PLAYBACK_START_EVENTS: ReadonlySet<string> = new Set([
  "output_audio_buffer.started",
  "response.audio.delta",
  "response.output_audio.delta",
  "response.audio_transcript.delta",
  "response.output_audio_transcript.delta",
]);

/** 播放闸门:远端默认静音;服务端放行后才允许下一次回应出声,插话只压住对应回应。 */
export interface PlaybackGate {
  /** 当前回应 id(增量事件缺 id 时兜底) */
  active: string | null;
  /** 被插话压住的回应 id 集合 */
  suppressed: ReadonlySet<string>;
  /** 插话时尚无回应 id 的兜底压制(下一个 response.created 起解除) */
  suppressAll: boolean;
  /** 服务端放行播报(仅问答结果);音频增量到达时才真正解除静音 */
  allowed: boolean;
}

export const INITIAL_PLAYBACK_GATE: PlaybackGate = {
  active: null,
  suppressed: new Set<string>(),
  suppressAll: false,
  allowed: false,
};

export type PlaybackSignal = "mute" | "unmute" | "finished" | "hold" | null;

const PLAYBACK_END_EVENTS: ReadonlySet<string> = new Set([
  "response.done",
  "output_audio_buffer.stopped",
  "output_audio_buffer.cleared",
]);

/** 事件所属回应 id:response.created 在 response.id,增量事件在 response_id。 */
function responseIdOf(event: unknown): string | null {
  if (typeof event !== "object" || event === null) return null;
  const e = event as { response_id?: unknown; response?: { id?: unknown } };
  if (typeof e.response_id === "string" && e.response_id) return e.response_id;
  if (typeof e.response?.id === "string" && e.response.id) return e.response.id;
  return null;
}

function eventTypeOf(event: unknown): string | null {
  if (typeof event !== "object" || event === null) return null;
  const type = (event as { type?: unknown }).type;
  return typeof type === "string" ? type : null;
}

/** 压住当前回应并撤销放行(插话/新问/停止到达;无 active 时兜底到下一次回应开始)。 */
export function suppressActivePlayback(gate: PlaybackGate): PlaybackGate {
  if (gate.active !== null) {
    return {
      active: gate.active,
      suppressed: new Set([...gate.suppressed, gate.active]),
      suppressAll: gate.suppressAll,
      allowed: false,
    };
  }
  return { ...gate, suppressAll: true, allowed: false };
}

/** 服务端放行:只对下一次回应生效;被压住的旧回应仍按 id 保持静音。 */
export function allowPlayback(gate: PlaybackGate): PlaybackGate {
  return { ...gate, allowed: true, suppressAll: false };
}

/** 回应结束:清掉该回应的压制标记;旧回应的收尾不影响当前新回应。 */
function reducePlaybackEnd(
  gate: PlaybackGate,
  event: unknown,
): { gate: PlaybackGate; signal: PlaybackSignal } {
  const id = responseIdOf(event) ?? gate.active;
  if (id !== null && gate.active !== null && id !== gate.active) {
    const suppressed = new Set(gate.suppressed);
    suppressed.delete(id);
    return { gate: { ...gate, suppressed }, signal: null };
  }
  const suppressed = new Set(gate.suppressed);
  if (id !== null) suppressed.delete(id);
  return {
    gate: {
      active: id !== null && id === gate.active ? null : gate.active,
      suppressed: id === null ? new Set<string>() : suppressed,
      suppressAll: false,
      allowed: false,
    },
    signal: "finished",
  };
}

/** 音频增量/起播:只放行未被压住的回应;缺 id 时用当前回应兜底。 */
function reducePlaybackStart(
  gate: PlaybackGate,
  event: unknown,
): { gate: PlaybackGate; signal: PlaybackSignal } {
  const id = responseIdOf(event) ?? gate.active;
  const blocked =
    !gate.allowed ||
    gate.suppressAll ||
    (id !== null ? gate.suppressed.has(id) : gate.suppressed.size > 0);
  return { gate, signal: blocked ? "hold" : "unmute" };
}

/** 模型事件 → 播放闸门与本地动作;被压住的回应即使还有音频增量也不解除静音。 */
export function reducePlayback(
  gate: PlaybackGate,
  event: unknown,
): { gate: PlaybackGate; signal: PlaybackSignal } {
  const type = eventTypeOf(event);
  if (type === null) return { gate, signal: null };
  if (type === "input_audio_buffer.speech_started") {
    return { gate: suppressActivePlayback(gate), signal: "mute" };
  }
  if (type === "response.created") {
    return { gate: { ...gate, active: responseIdOf(event), suppressAll: false }, signal: null };
  }
  if (PLAYBACK_END_EVENTS.has(type)) return reducePlaybackEnd(gate, event);
  if (PLAYBACK_START_EVENTS.has(type)) return reducePlaybackStart(gate, event);
  return { gate, signal: null };
}

/** 建立通话失败的原因:按来源给不同提示(麦克风被拒/无权限/未启用/已有通话/占满/网络受限/一般失败) */
export type FailureReason =
  "mic" | "forbidden" | "not_enabled" | "exists" | "busy" | "ice" | "network";

const MIC_ERROR_NAMES = new Set(["NotAllowedError", "NotFoundError", "NotReadableError"]);

const RAW_FAILURE_MAP: Record<string, FailureReason> = {
  VOICE_CALL_EXISTS: "exists",
  VOICE_BUSY: "busy",
  VOICE_NOT_ENABLED: "not_enabled",
  VOICE_ICE_FAILED: "ice",
};

/** 建交通话失败的分类:错误对象带 status 与 raw(后端错误码),麦克风错误是 DOMException。 */
export function classifyEstablishFailure(err: unknown): FailureReason {
  if (typeof err !== "object" || err === null) return "network";
  const e = err as { name?: unknown; status?: unknown; raw?: unknown };
  if (typeof e.name === "string" && MIC_ERROR_NAMES.has(e.name)) return "mic";
  if (e.status === 403) return "forbidden";
  if (typeof e.raw === "string" && e.raw in RAW_FAILURE_MAP) {
    return RAW_FAILURE_MAP[e.raw]!;
  }
  return "network";
}

/** 转移表:[当前状态, 事件] → 下一状态;缺表项=保持原状态。转移集中一处。 */
const PHASE_TRANSITIONS: ReadonlyMap<string, CallPhase> = new Map([
  ["hang_up:*", "idle"],
  ["channel_closed:*", "idle"],
  ["establish_failed:*", "failed"],
  ["establish_started:*", "connecting"],
  ["channel_open:connecting", "listening"],
  ["turn_requested:connecting", "querying"],
  ["turn_requested:listening", "querying"],
  ["turn_requested:querying", "querying"],
  ["turn_requested:speaking", "querying"],
  ["turn_stopped:querying", "listening"],
  ["turn_stopped:listening", "listening"],
  ["turn_stopped:speaking", "listening"],
  ["speech_started:speaking", "listening"],
  ["speech_started:querying", "listening"],
  ["playback_started:listening", "speaking"],
  ["playback_started:querying", "speaking"],
  ["playback_finished:speaking", "listening"],
]);

/** 通话状态归约:先精确转移,再退到通配项(*);页面只消费状态。 */
export function reduceCallPhase(phase: CallPhase, event: CallEvent): CallPhase {
  return (
    PHASE_TRANSITIONS.get(`${event.kind}:${phase}`) ??
    PHASE_TRANSITIONS.get(`${event.kind}:*`) ??
    phase
  );
}

/** 通道 WebSocket 地址:同源 /api 前缀(vite 代理对 api 前缀开启 ws 转发)。 */
export function channelUrl(origin: string, ticket: string): string {
  const wsOrigin = origin.replace(/^http/, "ws");
  return `${wsOrigin}/api/voice/calls/channel?ticket=${encodeURIComponent(ticket)}`;
}

/** 固定应答录音清单(与模型同一音色 Serena;按序轮换) */
export const FIXED_REPLIES: readonly string[] = [
  "/voice/zh-querying-one.m4a",
  "/voice/zh-querying-two.m4a",
];

/** 停止确认录音(与模型同一音色 Serena;停止意图由服务端判定后播放) */
export const STOP_REPLY = "/voice/zh-stopped.m4a";

/** 从 AnalyserNode 时域字节样本(0..255,静音 128)计算归一音量 [0, 1]。 */
export function computeAudioLevel(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sumSq = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const normalized = (samples[i]! - 128) / 128;
    sumSq += normalized * normalized;
  }
  const rms = Math.sqrt(sumSq / samples.length);
  // 过滤底噪并把日常说话 RMS 映射到明显可感的 [0, 1] 跳动区间
  if (rms < 0.012) return 0;
  return Math.min(1, rms * 3.5);
}

/** 音量包络平滑:起音快、回落慢,避免视觉跳动闪烁。 */
export function smoothAudioLevel(current: number, target: number): number {
  const clamped = Math.max(0, Math.min(1, target));
  const factor = clamped > current ? 0.42 : 0.16;
  const next = current + (clamped - current) * factor;
  return next < 0.005 ? 0 : next;
}
