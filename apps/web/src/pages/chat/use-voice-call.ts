/* 通话 hook:建交通话、凭票据连文字通道、浏览器直连、原样搬运、远端播报、音量采集与挂断清理。
   声音浏览器直连模型服务;决策全在后端(ADR-013);本 hook 只做搬运与媒体面。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { establishVoiceCall, hangUpVoiceCall, openVoiceChannel } from "@/lib/api";
import {
  allowPlayback,
  classifyEstablishFailure,
  classifyModelEvent,
  INITIAL_PLAYBACK_GATE,
  reduceCallPhase,
  reducePlayback,
  suppressActivePlayback,
  type CallEvent,
  type CallPhase,
  type FailureReason,
  type PlaybackGate,
} from "@/lib/voice-call";
import {
  attachRemoteAudio,
  createAudioMeter,
  setupDeferredMic,
  waitForIceGathering,
  type AudioLevels,
  type AudioMeter,
} from "./voice-call-media";

interface VoiceTurnRequest {
  token: string;
  question: string;
}

interface UseVoiceCallOptions {
  tenantId: string | null;
  /** 后端下发的 turn.request:页面播放固定应答并以听写原话发起问答(5.3)。 */
  onTurnRequest: (turn: VoiceTurnRequest) => void;
  /** 检测到说话开始:页面停止本地播放(即时打断感)。 */
  onSpeechStarted: () => void;
  /** 模型开始播报真实答案:页面立即停播本地固定应答,避免双声重叠。 */
  onPlaybackStarted?: () => void;
  /** 后端确认停止:页面停掉本地固定应答并播放固定停止确认。 */
  onTurnStopped?: () => void;
  /** 通话结束(含通道断开):页面回打字状态。 */
  onEnded: () => void;
}

interface PeerAndChannel {
  pc: RTCPeerConnection;
  ws: WebSocket;
  dataChannel: RTCDataChannel | null;
  stream: MediaStream;
  remoteAudio: HTMLAudioElement;
  meter: AudioMeter;
  dispose: () => void;
}

/** 释放通话相关的音轨、连接、AudioContext 与播放节点。 */
function releaseMedia(peer: PeerAndChannel | null): void {
  if (!peer) return;
  peer.dispose();
  peer.stream.getTracks().forEach((t) => t.stop());
  peer.remoteAudio.pause();
  peer.remoteAudio.srcObject = null;
  peer.meter.close();
  peer.dataChannel?.close();
  peer.ws.close();
  peer.pc.close();
}

interface ModelMessageContext {
  remoteAudio: HTMLAudioElement;
  /** 播放闸门(跨事件累积;被插话压住的回应不再解除静音) */
  gate: PlaybackGate;
  dispatch: (event: CallEvent) => void;
  onSpeechStarted: () => void;
  onPlaybackStarted?: () => void;
  onSessionUpdated?: () => void;
  relayToWs: (raw: string) => void;
}

/** 处理 DataChannel 收到的一条模型会话事件(本地静音/播报状态/转发后端)。 */
function handleModelMessage(rawData: string, ctx: ModelMessageContext): void {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawData);
  } catch {
    return;
  }
  const action = classifyModelEvent(parsed);
  const playback = reducePlayback(ctx.gate, parsed);
  ctx.gate = playback.gate;
  if (playback.signal === "mute") {
    ctx.remoteAudio.muted = true;
    ctx.dispatch({ kind: "speech_started" });
    ctx.onSpeechStarted();
  } else if (playback.signal === "unmute") {
    ctx.remoteAudio.muted = false;
    ctx.dispatch({ kind: "playback_started" });
    ctx.onPlaybackStarted?.();
  } else if (playback.signal === "hold") {
    // 未放行/被压住的音频增量:保持静音,不让残余音频漏出
    ctx.remoteAudio.muted = true;
  } else if (playback.signal === "finished") {
    ctx.remoteAudio.muted = true;
    ctx.dispatch({ kind: "playback_finished" });
  }
  const etype = (parsed as { type?: unknown }).type;
  if (etype === "session.updated") ctx.onSessionUpdated?.();
  if (action === "relay") ctx.relayToWs(rawData);
}

interface BridgeCallbacks {
  getOptions: () => UseVoiceCallOptions;
  dispatch: (event: CallEvent) => void;
  hangUp: () => Promise<void>;
  onRemoteDrop: (reason: FailureReason | null) => void;
}

interface ChannelMessageContext {
  msgCtx: ModelMessageContext;
  sendToModel: (payload: unknown) => void;
  onTurnRequest: (turn: VoiceTurnRequest) => void;
  onTurnStopped: () => void;
  onHangUp: () => Promise<void>;
}

/** 文字通道消息:模型转发、挂断,以及 turn.request(压住旧回答并保持静音)。 */
function handleChannelMessage(payload: unknown, ctx: ChannelMessageContext): void {
  const msg = payload as {
    type?: string;
    payload?: unknown;
    token?: string;
    question?: string;
  };
  if (msg.type === "model.send") {
    ctx.sendToModel(msg.payload);
    return;
  }
  if (msg.type === "call.end") {
    void ctx.onHangUp();
    return;
  }
  if (msg.type === "playback.allow") {
    // 仅放行下一次回应;真正解除静音仍等音频增量,避免旧回应残余音频漏出
    ctx.msgCtx.gate = allowPlayback(ctx.msgCtx.gate);
    return;
  }
  if (msg.type === "turn.stopped") {
    ctx.msgCtx.gate = suppressActivePlayback(ctx.msgCtx.gate);
    ctx.msgCtx.remoteAudio.muted = true;
    ctx.msgCtx.dispatch({ kind: "turn_stopped" });
    ctx.onTurnStopped();
    return;
  }
  if (msg.type !== "turn.request") return;
  // 新问到达:压住可能仍在播放的旧回答并保持静音,等新回答真正起播再解除
  ctx.msgCtx.gate = suppressActivePlayback(ctx.msgCtx.gate);
  ctx.msgCtx.remoteAudio.muted = true;
  ctx.msgCtx.dispatch({ kind: "turn_requested" });
  // 后端 BrowserSend 平铺下发 token/question;兼容嵌套 payload 以免测试桩或旧包装失配
  const nested =
    msg.payload && typeof msg.payload === "object"
      ? (msg.payload as { token?: string; question?: string })
      : null;
  ctx.onTurnRequest({
    token: String(msg.token ?? nested?.token ?? ""),
    question: String(msg.question ?? nested?.question ?? ""),
  });
}

/** 建立本地媒体、WebRTC 对等连接与 WebSocket 文字通道的完整桥接。 */
async function connectVoicePeer(
  stream: MediaStream,
  meter: AudioMeter,
  cb: BridgeCallbacks,
): Promise<{ peer: PeerAndChannel; callId: string }> {
  const pc = new RTCPeerConnection();
  const mic = setupDeferredMic(pc, stream);
  const remoteAudio = attachRemoteAudio(pc, meter);

  // 标准 Realtime DataChannel 名称 oai-events;两端队列消除 WS/DC 就绪竞态
  let activeChannel: RTCDataChannel = pc.createDataChannel("oai-events");
  const pendingModelQueue: string[] = [];
  const pendingWsQueue: string[] = [];
  let ws: WebSocket | null = null;
  let closedByUs = false;
  let channelOpened = false;
  let channelClosed = false;
  // 牵线成功即占用了服务端通话与准入；后续任何建立失败都要显式挂断，避免幽灵占用
  let establishedCallId: string | null = null;

  const flushModelQueue = () => {
    mic.armFallback();
    while (activeChannel.readyState === "open" && pendingModelQueue.length > 0) {
      const raw = pendingModelQueue.shift();
      if (raw) activeChannel.send(raw);
    }
  };
  const sendToModel = (payload: unknown) => {
    const raw = JSON.stringify(payload);
    if (activeChannel.readyState === "open") activeChannel.send(raw);
    else pendingModelQueue.push(raw);
  };
  const sendToWs = (raw: string) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(raw);
    else pendingWsQueue.push(raw);
  };
  const msgCtx: ModelMessageContext = {
    remoteAudio,
    gate: INITIAL_PLAYBACK_GATE,
    dispatch: cb.dispatch,
    onSpeechStarted: () => cb.getOptions().onSpeechStarted(),
    onPlaybackStarted: () => cb.getOptions().onPlaybackStarted?.(),
    onSessionUpdated: mic.attach,
    relayToWs: sendToWs,
  };

  const bindDataChannel = (dc: RTCDataChannel) => {
    activeChannel = dc;
    dc.onopen = flushModelQueue;
    dc.onmessage = (ev) => handleModelMessage(ev.data as string, msgCtx);
  };

  bindDataChannel(activeChannel);
  pc.ondatachannel = (ev) => {
    // 仅当客户端发起的通道尚未就绪且名称匹配时才接管,避免覆盖已打开的 oai-events 通道
    if (activeChannel.readyState !== "open" && ev.channel.label === "oai-events") {
      bindDataChannel(ev.channel);
      flushModelQueue();
    } else {
      ev.channel.onmessage = (msgEv) => handleModelMessage(msgEv.data as string, msgCtx);
    }
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitForIceGathering(pc);

    const established = await establishVoiceCall(
      pc.localDescription?.sdp ?? "",
      cb.getOptions().tenantId,
      new AbortController().signal,
    );
    establishedCallId = established.call_id;
    await pc.setRemoteDescription({ type: "answer", sdp: established.answer });

    ws = openVoiceChannel(
      established.ticket,
      (payload: unknown) =>
        handleChannelMessage(payload, {
          msgCtx,
          sendToModel,
          onTurnRequest: (turn) => cb.getOptions().onTurnRequest(turn),
          onTurnStopped: () => cb.getOptions().onTurnStopped?.(),
          onHangUp: cb.hangUp,
        }),
      () => {
        if (closedByUs) return;
        channelClosed = true;
        closedByUs = true;
        // 通道未打开就关闭(票据过期/服务端拒绝):服务端通道收口不一定执行，
        // 主动挂断把已建立的通话与准入一起释放
        if (!channelOpened && establishedCallId) void hangUpVoiceCall(establishedCallId);
        cb.onRemoteDrop(null);
      },
    );

    ws.onopen = () => {
      channelOpened = true;
      while (ws && ws.readyState === WebSocket.OPEN && pendingWsQueue.length > 0) {
        const raw = pendingWsQueue.shift();
        if (raw) ws.send(raw);
      }
    };

    const onPeerFailure = () => {
      if (closedByUs) return;
      if (pc.connectionState === "failed" || pc.iceConnectionState === "failed") {
        closedByUs = true;
        cb.onRemoteDrop("ice");
      }
    };
    pc.addEventListener("connectionstatechange", onPeerFailure);
    pc.addEventListener("iceconnectionstatechange", onPeerFailure);
    if (channelClosed) {
      throw new Error("通话通道已关闭");
    }

    return {
      callId: established.call_id,
      peer: {
        pc,
        ws,
        dataChannel: activeChannel,
        stream,
        remoteAudio,
        meter,
        dispose: () => {
          closedByUs = true;
          mic.cancel();
          pc.removeEventListener("connectionstatechange", onPeerFailure);
          pc.removeEventListener("iceconnectionstatechange", onPeerFailure);
        },
      },
    };
  } catch (err) {
    mic.cancel();
    remoteAudio.pause();
    remoteAudio.srcObject = null;
    ws?.close();
    pc.close();
    if (establishedCallId) void hangUpVoiceCall(establishedCallId);
    throw err;
  }
}

export function useVoiceCall(options: UseVoiceCallOptions) {
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const [phase, setPhase] = useState<CallPhase>("idle");
  const [failure, setFailure] = useState<FailureReason | null>(null);
  const peerRef = useRef<PeerAndChannel | null>(null);
  const callIdRef = useRef<string | null>(null);
  const phaseRef = useRef<CallPhase>("idle");
  // 拨号中标记与通话序号:防止连点重复建连,并使拨号中途挂断/卸载能立即废止后续流程
  const startingRef = useRef(false);
  const callSeqRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      callSeqRef.current += 1;
      const peer = peerRef.current;
      peerRef.current = null;
      const callId = callIdRef.current;
      callIdRef.current = null;
      releaseMedia(peer);
      if (callId) void hangUpVoiceCall(callId);
    };
  }, []);

  const dispatch = useCallback((event: CallEvent) => {
    phaseRef.current = reduceCallPhase(phaseRef.current, event);
    setPhase(phaseRef.current);
  }, []);

  const sampleAudioLevels = useCallback((): AudioLevels => {
    return peerRef.current?.meter.sample() ?? { user: 0, model: 0 };
  }, []);

  const hangUp = useCallback(async () => {
    callSeqRef.current += 1;
    dispatch({ kind: "hang_up" });
    const peer = peerRef.current;
    peerRef.current = null;
    const callId = callIdRef.current;
    callIdRef.current = null;
    // 先让服务端记「挂断」再收通道：否则 ws.close 先到会把审计记成通道断开
    if (peer) {
      peer.remoteAudio.muted = true;
      peer.remoteAudio.pause();
    }
    if (callId) await hangUpVoiceCall(callId);
    releaseMedia(peer);
    optionsRef.current.onEnded();
  }, [dispatch]);

  const start = useCallback(async () => {
    if (peerRef.current || startingRef.current) return;
    startingRef.current = true;
    const seq = ++callSeqRef.current;
    setFailure(null);
    dispatch({ kind: "establish_started" });

    let stream: MediaStream | null = null;
    let meter: AudioMeter | null = null;
    try {
      // 麦克风(通话必须由点击手势发起,浏览器自动播放限制同此;显式开启回声消除与降噪减少播报回声误断句)
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (!mountedRef.current || callSeqRef.current !== seq) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      meter = createAudioMeter(stream);
      const { peer, callId } = await connectVoicePeer(stream, meter, {
        getOptions: () => optionsRef.current,
        dispatch,
        hangUp,
        onRemoteDrop: (reason) => {
          const current = peerRef.current;
          peerRef.current = null;
          const activeId = callIdRef.current;
          callIdRef.current = null;
          releaseMedia(current);
          if (reason) {
            if (activeId) void hangUpVoiceCall(activeId);
            dispatch({ kind: "establish_failed" });
            setFailure(reason);
          } else {
            dispatch({ kind: "channel_closed" });
          }
          optionsRef.current.onEnded();
        },
      });
      if (!mountedRef.current || callSeqRef.current !== seq) {
        releaseMedia(peer);
        void hangUpVoiceCall(callId);
        return;
      }
      callIdRef.current = callId;
      peerRef.current = peer;
      dispatch({ kind: "channel_open" });
    } catch (err) {
      if (!mountedRef.current || callSeqRef.current !== seq) {
        stream?.getTracks().forEach((t) => t.stop());
        meter?.close();
        return;
      }
      dispatch({ kind: "establish_failed" });
      if (peerRef.current) {
        const current = peerRef.current;
        peerRef.current = null;
        releaseMedia(current);
      } else {
        stream?.getTracks().forEach((t) => t.stop());
        meter?.close();
      }
      setFailure(classifyEstablishFailure(err));
      optionsRef.current.onEnded();
    } finally {
      startingRef.current = false;
    }
  }, [dispatch, hangUp]);

  return { phase, failure, start, hangUp, sampleAudioLevels };
}
