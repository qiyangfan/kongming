/* 在途流的数据类型：发起态、发起上下文、待确认卡与外部入参、对外 API 契约。 */

import type { ChatQueryOut } from "@/lib/api";

/** 在途流状态:发起时一次性创建,过程事件不经它广播 */
export interface StreamingTurnStart {
  question: string;
  body: unknown;
  attSnapshot: { id: string; filename: string }[];
  ac: AbortController;
}

export interface ChatCtx {
  sessionIdRef: React.RefObject<string | null>;
  tenantRef: React.RefObject<string | null>;
  dsRef: React.RefObject<string | null>;
  cancelRecording: () => void;
}

export interface PendingConfirm {
  pending: ChatQueryOut["pending_tool_calls"];
  sessionId: string;
}

export interface ChatStreamApi {
  streaming: StreamingTurnStart | null;
  setStreaming: React.Dispatch<React.SetStateAction<StreamingTurnStart | null>>;
  finalized: { question: string; out: ChatQueryOut } | null;
  setFinalized: React.Dispatch<
    React.SetStateAction<{ question: string; out: ChatQueryOut } | null>
  >;
  stoppedNotice: boolean;
  setStoppedNotice: React.Dispatch<React.SetStateAction<boolean>>;
  pendingConfirm: PendingConfirm | null;
  setPendingConfirm: React.Dispatch<React.SetStateAction<PendingConfirm | null>>;
  forceBottomRef: React.MutableRefObject<boolean>;
  finalizeOk: (out: ChatQueryOut, question: string) => void;
  finalizeError: (
    message: string,
    question: string,
    attSnapshot?: { id: string; filename: string }[],
  ) => void;
  submit: (text: string | undefined, inputs: StreamInputs) => void;
  stopStreaming: () => void;
  /** 取消闸：中止在途流并清空残留呈现（上下文切换/卸载时调用）。 */
  resetStream: () => void;
  selfNavigatedRef: React.MutableRefObject<string | null>;
  shutdown: () => void;
  abortRef: React.MutableRefObject<AbortController | null>;
  streamCtxRef: React.MutableRefObject<{
    sid: string | null;
    tenant: string | null;
    ds: string | null;
  } | null>;
}

export interface StreamInputs {
  /** 发送时的草稿与附件（chips 直发时草稿保留）。 */
  draft: string;
  attachments: { id: string; filename: string }[];
  uploading: boolean;
  agentMode: string;
}
