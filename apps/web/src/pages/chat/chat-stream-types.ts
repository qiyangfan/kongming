/* 在途流的数据类型：发起态、发起上下文、待确认卡与外部入参、对外 API 契约。 */

import type { ChatQueryOut } from "@/lib/api";
import type { StopNotice } from "@/lib/turn-stop";

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

/** 流发起时的上下文(守卫基准) */
export interface StreamCtx {
  sid: string | null;
  tenant: string | null;
  ds: string | null;
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
  /** 停止后的提示依据(取消结果);null=无提示 */
  stoppedNotice: StopNotice | null;
  setStoppedNotice: React.Dispatch<React.SetStateAction<StopNotice | null>>;
  pendingConfirm: PendingConfirm | null;
  setPendingConfirm: React.Dispatch<React.SetStateAction<PendingConfirm | null>>;
  forceBottomRef: React.RefObject<boolean>;
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
  selfNavigatedRef: React.RefObject<string | null>;
  shutdown: () => void;
  abortRef: React.RefObject<AbortController | null>;
  /** 本轮标识(X-Trace-Id),响应头到达后写入;停止时据此向控制面发起取消 */
  traceIdRef: React.RefObject<string | null>;
  streamCtxRef: React.RefObject<StreamCtx | null>;
}

export interface StreamInputs {
  /** 发送时的草稿与附件（chips 直发时草稿保留）。 */
  draft: string;
  attachments: { id: string; filename: string }[];
  uploading: boolean;
  agentMode: string;
}
