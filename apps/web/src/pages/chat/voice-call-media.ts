/* 语音通话媒体底层工具:Web Audio 音量分析器、ICE 收集等待、延迟麦轨挂载与远端播放节点。 */

import { computeAudioLevel } from "@kongming/api-client/voice-call";

export interface AudioLevels {
  user: number;
  model: number;
}

export interface AudioMeter {
  attachRemote: (stream: MediaStream) => void;
  sample: () => AudioLevels;
  close: () => void;
}

interface DeferredMicHandle {
  attach: () => void;
  armFallback: () => void;
  cancel: () => void;
}

const ICE_GATHER_TIMEOUT_MS = 3000;

/** 等待本地 SDP 完成 ICE 候选收集(HTTP POST 非 trickle 牵线必须带候选)。 */
export function waitForIceGathering(
  pc: RTCPeerConnection,
  timeoutMs = ICE_GATHER_TIMEOUT_MS,
): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      pc.removeEventListener("icegatheringstatechange", onState);
      resolve();
    };
    const onState = () => {
      if (pc.iceGatheringState === "complete") done();
    };
    const timer = setTimeout(done, timeoutMs);
    pc.addEventListener("icegatheringstatechange", onState);
  });
}

/** 创建基于 Web Audio AnalyserNode 的本地/远端实时音量采集器。 */
export function createAudioMeter(localStream: MediaStream): AudioMeter {
  const AudioCtx =
    typeof window !== "undefined"
      ? (window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)
      : undefined;
  if (!AudioCtx) {
    return {
      attachRemote: () => undefined,
      sample: () => ({ user: 0, model: 0 }),
      close: () => undefined,
    };
  }
  let ctx: AudioContext | null = null;
  try {
    ctx = new AudioCtx();
    void ctx.resume().catch(() => undefined);
  } catch {
    return {
      attachRemote: () => undefined,
      sample: () => ({ user: 0, model: 0 }),
      close: () => undefined,
    };
  }
  const userAnalyser = ctx.createAnalyser();
  userAnalyser.fftSize = 256;
  userAnalyser.smoothingTimeConstant = 0.5;
  ctx.createMediaStreamSource(localStream).connect(userAnalyser);
  const userBuf = new Uint8Array(userAnalyser.frequencyBinCount);

  let remoteAnalyser: AnalyserNode | null = null;
  let remoteBuf: Uint8Array<ArrayBuffer> | null = null;

  return {
    attachRemote: (remoteStream: MediaStream) => {
      if (!ctx || ctx.state === "closed" || remoteAnalyser) return;
      try {
        remoteAnalyser = ctx.createAnalyser();
        remoteAnalyser.fftSize = 256;
        remoteAnalyser.smoothingTimeConstant = 0.5;
        ctx.createMediaStreamSource(remoteStream).connect(remoteAnalyser);
        remoteBuf = new Uint8Array(remoteAnalyser.frequencyBinCount);
      } catch {
        remoteAnalyser = null;
        remoteBuf = null;
      }
    },
    sample: () => {
      userAnalyser.getByteTimeDomainData(userBuf);
      const user = computeAudioLevel(userBuf);
      let model = 0;
      if (remoteAnalyser && remoteBuf) {
        remoteAnalyser.getByteTimeDomainData(remoteBuf);
        model = computeAudioLevel(remoteBuf);
      }
      return { user, model };
    },
    close: () => {
      if (ctx && ctx.state !== "closed") {
        void ctx.close().catch(() => undefined);
      }
      ctx = null;
    },
  };
}

/** 延迟挂载麦克风轨道:先以空 sendrecv transceiver 建连,待 session.updated 就绪后再 replaceTrack,避免模型在 turn_detection 配置完成前收到音频包。 */
export function setupDeferredMic(pc: RTCPeerConnection, stream: MediaStream): DeferredMicHandle {
  const micTrack = stream.getAudioTracks?.()[0] ?? stream.getTracks()[0];
  if (!micTrack) {
    return { attach: () => undefined, armFallback: () => undefined, cancel: () => undefined };
  }
  let attached = false;
  let fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  const clearTimer = () => {
    if (fallbackTimer !== null) {
      clearTimeout(fallbackTimer);
      fallbackTimer = null;
    }
  };
  let attachImpl: () => void;
  if (typeof pc.addTransceiver === "function") {
    const transceiver = pc.addTransceiver("audio", { direction: "sendrecv" });
    attachImpl = () => {
      if (attached) return;
      attached = true;
      clearTimer();
      void transceiver.sender.replaceTrack(micTrack).catch(() => undefined);
    };
  } else {
    micTrack.enabled = false;
    pc.addTrack(micTrack, stream);
    attachImpl = () => {
      if (attached) return;
      attached = true;
      clearTimer();
      micTrack.enabled = true;
    };
  }
  return {
    attach: attachImpl,
    armFallback: () => {
      if (attached || fallbackTimer !== null) return;
      fallbackTimer = setTimeout(attachImpl, 2500);
    },
    cancel: clearTimer,
  };
}

/** 创建远端语音播放节点并绑定 RTCPeerConnection.ontrack 与音量分析器。 */
export function attachRemoteAudio(pc: RTCPeerConnection, meter: AudioMeter): HTMLAudioElement {
  const remoteAudio = new Audio();
  remoteAudio.autoplay = true;
  // 默认静音:只有服务端放行且音频真正起播时才解除,防止模型自答漏出
  remoteAudio.muted = true;
  (remoteAudio as HTMLAudioElement & { playsInline?: boolean }).playsInline = true;
  pc.ontrack = (ev) => {
    const remoteStream = ev.streams[0] ?? new MediaStream([ev.track]);
    remoteAudio.srcObject = remoteStream;
    void remoteAudio.play()?.catch(() => undefined);
    meter.attachRemote(remoteStream);
  };
  return remoteAudio;
}
