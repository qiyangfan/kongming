/* 在途流：发起、终态接管与真取消(停止即向控制面发起取消)；流回调仅在发起上下文与当前一致时生效。 */

import { useRef, useState } from "react";
import type { RefObject } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import type { ChatQueryOut } from "@/lib/api";
import type { StopNotice } from "@/lib/turn-stop";

import {
  buildStreamBody,
  makeFinalizeError,
  makeFinalizeOk,
  makeOriginGuard,
  makeStopTurn,
} from "./chat-stream-actions";
import type {
  ChatCtx,
  ChatStreamApi,
  PendingConfirm,
  StreamCtx,
  StreamInputs,
  StreamingTurnStart,
} from "./chat-stream-types";

export type { PendingConfirm, StreamingTurnStart } from "./chat-stream-types";

/** 上下文切换/卸载时的流回收动作(不触发无关状态更新)。 */
function makeContextResetters(env: {
  abortRef: RefObject<AbortController | null>;
  streamCtxRef: RefObject<StreamCtx | null>;
  stopSeqRef: RefObject<number>;
  cancelRecording: () => void;
  setStreaming: (v: StreamingTurnStart | null) => void;
  setFinalized: (v: { question: string; out: ChatQueryOut } | null) => void;
  setAskError: (m: string | null) => void;
  setStoppedNotice: (v: StopNotice | null) => void;
  setPendingConfirm: (v: PendingConfirm | null) => void;
}) {
  /** 取消闸：中止在途流并清残留呈现，同时终止录音。 */
  function resetStream(): void {
    env.abortRef.current?.abort();
    env.abortRef.current = null;
    env.streamCtxRef.current = null;
    // 录音不跨上下文续命:切会话/租户/数据源即取消(防串写新输入框)
    env.cancelRecording();
    env.setStreaming(null);
    env.setFinalized(null);
    env.setAskError(null);
    env.setStoppedNotice(null);
    env.stopSeqRef.current += 1; // 迟到的取消应答不再落到新上下文
    env.setPendingConfirm(null); // 切换上下文丢弃待确认入口(瞬态语义)
  }

  /** 卸载时只回收硬件与流，不触发状态更新。 */
  function shutdown(): void {
    env.abortRef.current?.abort();
    env.abortRef.current = null;
    env.cancelRecording();
  }

  return { resetStream, shutdown };
}

/** 在途流、终态快照与待确认卡等状态及其引用。 */
function useStreamState() {
  const [streaming, setStreaming] = useState<StreamingTurnStart | null>(null);
  // 本轮终态快照:result 到达后先用快照渲染,消息历史载入该轮后替换,避免中间空白
  const [finalized, setFinalized] = useState<{ question: string; out: ChatQueryOut } | null>(null);
  // 主动停止后的提示:文案随取消结果如实变化(未确认前不声称已取消)
  const [stoppedNotice, setStoppedNotice] = useState<StopNotice | null>(null);
  // 待确认写工具(独立于 finalized 快照:快照退位由历史接管后确认卡
  // 仍在,直到用户确认/拒绝或切换上下文——响应级瞬态,不落库不回显)
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 本轮标识(X-Trace-Id):响应头到达后由流组件写入,停止时据此发起取消
  const traceIdRef = useRef<string | null>(null);
  // 最近一次停止的序号:取消应答只作用于发起它的那一次停止
  const stopSeqRef = useRef(0);
  // 流发起时的上下文(守卫基准):迟到事件与当前不一致即丢弃(防串上下文)
  const streamCtxRef = useRef<StreamCtx | null>(null);
  // 本流自建会话的导航标记(取消闸豁免依据)
  const selfNavigatedRef = useRef<string | null>(null);
  // 强制贴底标记:切会话/用户主动提问时消费(内容 effect 先于跟随判定)
  const forceBottomRef = useRef(false);
  // 每次发起递增的在途轮序号:供 ChatThread 作为 key,确保同帧顶掉旧轮时重建 StreamingTurn
  const turnSeqRef = useRef(0);
  return [
    streaming,
    setStreaming,
    finalized,
    setFinalized,
    stoppedNotice,
    setStoppedNotice,
    pendingConfirm,
    setPendingConfirm,
    abortRef,
    traceIdRef,
    stopSeqRef,
    streamCtxRef,
    selfNavigatedRef,
    forceBottomRef,
    turnSeqRef,
  ] as const;
}

/** 流式问答状态机；上下文引用由调用方以 ref 提供，避免回调读到旧值。 */
export function useChatStream(
  ctx: ChatCtx,
  qc: QueryClient,
  callbacks: {
    setDraft: React.Dispatch<React.SetStateAction<string>>;
    setAskError: (message: string | null) => void;
    setAttachments: React.Dispatch<React.SetStateAction<{ id: string; filename: string }[]>>;
    setFollowBottom: (near: boolean) => void;
  },
): ChatStreamApi {
  const { setDraft, setAskError, setAttachments, setFollowBottom } = callbacks;
  const navigate = useNavigate();
  const { sessionIdRef, tenantRef, dsRef } = ctx;
  const [
    streaming,
    setStreaming,
    finalized,
    setFinalized,
    stoppedNotice,
    setStoppedNotice,
    pendingConfirm,
    setPendingConfirm,
    abortRef,
    traceIdRef,
    stopSeqRef,
    streamCtxRef,
    selfNavigatedRef,
    forceBottomRef,
    turnSeqRef,
  ] = useStreamState();

  const inOriginCtx = makeOriginGuard(streamCtxRef, ctx);

  const finalizeOk = makeFinalizeOk({
    ctx,
    qc,
    navigate,
    inOriginCtx,
    setStreaming,
    setStoppedNotice,
    setAskError,
    setPendingConfirm,
    setFinalized,
    selfNavigatedRef,
  });

  const finalizeError = makeFinalizeError({
    inOriginCtx,
    setStreaming,
    setAskError,
    setDraft,
    setAttachments,
  });

  /** 发起流：记录发起上下文并创建在途态，由 StreamingTurn 消费。 */
  function submit(text: string | undefined, inputs: StreamInputs): void {
    const { draft, attachments, uploading } = inputs;
    // 建议 chips 直发(text 显式传入,保留草稿不被清空);缺省=输入框草稿
    const fromDraft = text === undefined;
    const q = (text ?? draft).trim();
    // question 至少包含一个非空字符；上传未完成时不提交,避免请求遗漏附件。
    if (!q || uploading) return;
    // 语音新问顶掉旧轮(旧轮由 onTurnRequest 显式取消收口);打字轮仍防重复提交
    if (streaming !== null && !inputs.voiceTurnToken) return;
    const attIds = attachments.map((a) => a.id);
    const attSnapshot = attachments; // 失败恢复用快照
    setAttachments([]);
    // 草稿直发即清空；建议 chips 直发保留用户未发送的草稿
    if (fromDraft) setDraft("");
    setAskError(null);
    setStoppedNotice(null);
    setFinalized(null); // 新一轮提问顶掉上一轮快照
    stopSeqRef.current += 1; // 上一轮若仍有取消应答在途,不再落到本轮
    traceIdRef.current = null;
    const ac = new AbortController();
    abortRef.current = ac;
    streamCtxRef.current = {
      sid: sessionIdRef.current,
      tenant: tenantRef.current,
      ds: dsRef.current,
    };
    // 在途态一次性创建,占位只插一次;过程事件由 StreamingTurn 自持
    setStreaming({
      id: ++turnSeqRef.current,
      question: q,
      ac,
      attSnapshot,
      body: buildStreamBody({
        question: q,
        inputs,
        attIds,
        sessionId: sessionIdRef.current,
        ds: dsRef.current,
      }),
    });
    // 主动提问:强制贴底(用户明确要从最新开始看本轮)
    forceBottomRef.current = true;
    setFollowBottom(true);
  }

  const stopTurn = makeStopTurn({ traceIdRef, stopSeqRef, setStoppedNotice, setDraft });

  /** 停止:中止读取并立即释放输入;取消请求与结果提示由 stopTurn 收口。 */
  function stopStreaming(options?: { restoreDraft?: boolean }): void {
    const s = streaming;
    abortRef.current?.abort();
    abortRef.current = null;
    streamCtxRef.current = null;
    setStreaming(null);
    stopTurn(s, options);
  }

  const { resetStream, shutdown } = makeContextResetters({
    abortRef,
    streamCtxRef,
    stopSeqRef,
    cancelRecording: ctx.cancelRecording,
    setStreaming,
    setFinalized,
    setAskError,
    setStoppedNotice,
    setPendingConfirm,
  });

  return {
    streaming,
    setStreaming,
    finalized,
    setFinalized,
    stoppedNotice,
    setStoppedNotice,
    pendingConfirm,
    setPendingConfirm,
    forceBottomRef,
    finalizeOk,
    finalizeError,
    submit,
    stopStreaming,
    resetStream,
    shutdown,
    abortRef,
    traceIdRef,
    streamCtxRef,
    selfNavigatedRef,
  };
}
