/* 流的终态接管与停止:成功/失败收口;回调只在流发起上下文仍为当前上下文时生效。 */

import type { QueryClient } from "@tanstack/react-query";
import type { NavigateFunction } from "react-router";

import type { ChatQueryOut } from "@/lib/api";

import type { ChatCtx, StreamInputs, StreamingTurnStart } from "./chat-stream-types";

interface FinalizeOkDeps {
  ctx: ChatCtx;
  qc: QueryClient;
  navigate: NavigateFunction;
  inOriginCtx: () => boolean;
  setStreaming: (value: StreamingTurnStart | null) => void;
  setStoppedNotice: (value: boolean) => void;
  setAskError: (message: string | null) => void;
  setPendingConfirm: (
    value: { pending: ChatQueryOut["pending_tool_calls"]; sessionId: string } | null,
  ) => void;
  setFinalized: (value: { question: string; out: ChatQueryOut } | null) => void;
  selfNavigatedRef: React.RefObject<string | null>;
}

/** 生成成功终态接管函数：清在途态、留待确认入口、导航到会话并失效侧栏与消息缓存。 */
export function makeFinalizeOk(deps: FinalizeOkDeps) {
  const { ctx, qc, navigate, inOriginCtx, selfNavigatedRef } = deps;
  const { sessionIdRef, tenantRef, dsRef } = ctx;
  return (out: ChatQueryOut, question: string): void => {
    if (!inOriginCtx()) return;
    deps.setStreaming(null);
    deps.setStoppedNotice(false);
    deps.setAskError(null);
    // 待确认调用独立留存(快照退位不带走确认入口);
    // ?? [] 兜底:前后端版本错位(滚动部署)缺键时不炸
    const pendingCalls = out.pending_tool_calls ?? [];
    if (pendingCalls.length > 0 && out.session_id) {
      deps.setPendingConfirm({ pending: pendingCalls, sessionId: out.session_id });
    }
    deps.setFinalized({ question, out }); // 乐观渲染本轮,历史 refetch 后替换
    // 先导航(消空态闪回),再失效侧栏;自建导航打标,取消闸据此豁免
    if (out.session_id && out.session_id !== sessionIdRef.current) {
      selfNavigatedRef.current = out.session_id;
      void navigate(`/chat/${out.session_id}`, { replace: true });
    }
    void qc.invalidateQueries({ queryKey: ["sessions", tenantRef.current] });
    if (out.session_id === sessionIdRef.current) {
      void qc.invalidateQueries({
        queryKey: ["messages", out.session_id, tenantRef.current, dsRef.current],
      });
    }
  };
}

interface FinalizeErrorDeps {
  inOriginCtx: () => boolean;
  setStreaming: (value: StreamingTurnStart | null) => void;
  setAskError: (message: string | null) => void;
  setDraft: React.Dispatch<React.SetStateAction<string>>;
  setAttachments: React.Dispatch<React.SetStateAction<{ id: string; filename: string }[]>>;
}

/** 生成失败终态接管函数：问句回填输入框，附件恢复可重试。 */
export function makeFinalizeError(deps: FinalizeErrorDeps) {
  return (
    message: string,
    question: string,
    attSnapshot: { id: string; filename: string }[] = [],
  ): void => {
    if (!deps.inOriginCtx()) return;
    deps.setStreaming(null);
    deps.setAskError(message);
    // 失败回填,但尊重用户等待期间的新输入
    deps.setDraft((prev) => (prev.trim() ? prev : question));
    // 传输层失败时附件未被消费(agent 未取件),chips 恢复可重试;
    // 尊重等待期间用户新贴的附件(去重合并)
    if (attSnapshot.length) {
      deps.setAttachments((prev) => {
        const seen = new Set(prev.map((a) => a.id));
        return [...prev, ...attSnapshot.filter((a) => !seen.has(a.id))];
      });
    }
  };
}

/** 组装发往 agent 的问答请求体:附件与会话仅在存在时携带。 */
export function buildStreamBody(req: {
  question: string;
  inputs: StreamInputs;
  attIds: string[];
  sessionId: string | null;
  ds: string | null;
}): Record<string, unknown> {
  return {
    question: req.question,
    datasource_key: req.ds,
    agent_mode: req.inputs.agentMode, // ReAct 内核开关,缺省 recipe
    ...(req.attIds.length ? { attachment_ids: req.attIds } : {}),
    ...(req.sessionId ? { session_id: req.sessionId } : {}),
  };
}
