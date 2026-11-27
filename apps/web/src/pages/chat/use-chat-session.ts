/* 对话页的会话侧数据与副作用:消息/建议查询、删除收敛、上下文取消闸、贴底跟随与中断恢复回查。 */

import { useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import { api, type SessionMessageOut } from "@kongming/api-client/api";
import { getSuggestions } from "@kongming/api-client/chat-api";
import {
  RECOVERY_POLL_BUDGET_MS,
  RECOVERY_POLL_INTERVAL_MS,
  recoveryExpired,
  needsTurnRecovery,
} from "@kongming/api-client/recovery-poll";

import { toSyntheticMessage } from "./message-row";
import { useFollowBottom, type ChatScrollApi } from "./use-chat-scroll";
import type { ChatStreamApi } from "./chat-stream-types";

/** 判断请求错误是否带指定 HTTP 状态码。 */
function isStatus(err: unknown, status: number): boolean {
  return (err as Error & { status?: number }).status === status;
}

interface ChatSessionParams {
  sessionId: string | undefined;
  current: string | null;
  datasource: string | null;
  askError: string | null;
  stream: ChatStreamApi;
}

/** 订阅会话消息与冷启动建议，并驱动与流状态联动的副作用（删除收敛、取消闸、恢复回查、快照退位）。 */
export function useChatSession(params: ChatSessionParams & { scroll: ChatScrollApi }) {
  const { sessionId, current, datasource, askError, stream, scroll } = params;
  const qc = useQueryClient();
  const navigate = useNavigate();

  const messages = useQuery({
    // 键含租户+数据源双维度:切换即换档,不跨上下文残留
    queryKey: ["messages", sessionId ?? "-", current, datasource],
    queryFn: () => api.get<SessionMessageOut[]>(`/chat/sessions/${sessionId}/messages`),
    enabled: sessionId !== undefined,
  });

  // 冷启动建议:仅新建态干净分支启用;键含租户+数据源双维度,切换即换档
  const suggestions = useQuery({
    queryKey: ["chat-suggestions", current, datasource],
    // 新建态下数据源可能尚未选定,此时不发起建议请求
    queryFn: () => getSuggestions(datasource!),
    enabled: !sessionId && !stream.streaming && !stream.finalized && !askError,
  });

  // 会话已被删除(他端/本端竞态):提示并收敛回新建态,不留空白死态
  useEffect(() => {
    if (messages.isError && isStatus(messages.error, 404)) {
      void qc.invalidateQueries({ queryKey: ["sessions", current] });
      void navigate("/chat", { replace: true });
    }
  }, [messages.isError, messages.error, qc, navigate, current]);

  useContextReset(params);
  useRouteShutdown(stream);
  useRecoveryPoll({ sessionId, messagesData: messages.data, refetch: messages.refetch });
  // 停止收口后刷新一次列表:服务端该轮已落定终态(已取消/已结束),而在途态与
  // 快照都不含该行——不回查则已取消卡片不出现(实测:列表末条仍是上一轮答句,
  // 不满足 078 回查条件)。取消失效(仍在后台跑)时刷新后末条即问句,交由
  // 078 回查继续跟到终态;会话列表一并刷新,新会话得以出现在侧栏。
  useEffect(() => {
    const notice = stream.stoppedNotice;
    if (!notice || notice.kind === "pending") return;
    void qc.invalidateQueries({ queryKey: ["messages", sessionId ?? "-", current, datasource] });
    void qc.invalidateQueries({ queryKey: ["sessions", current] });
  }, [stream.stoppedNotice, qc, sessionId, current, datasource]);
  useSnapshotRetire(stream, messages.data);
  // ScrollArea 视口仅在挂载后可用，内容变化后由该 hook 跟随贴底
  useFollowBottom({
    viewportRef: scroll.viewportRef,
    followBottom: scroll.followBottom,
    forceBottomRef: stream.forceBottomRef,
    deps: [messages.data, stream.streaming, scroll.followBottom, sessionId],
  });

  // 快照消息 useMemo 稳定引用(finalized 对象仅在轮次边界变)
  const finalizedMsg = useMemo(
    () => (stream.finalized ? toSyntheticMessage(stream.finalized.out) : null),
    [stream.finalized],
  );

  return { messages, suggestions, finalizedMsg };
}

/** 切换会话强制贴底(用户主动进入,从最新开始读)。 */
export function useForceBottomOnSessionChange(
  sessionId: string | undefined,
  stream: ChatStreamApi,
  scroll: ChatScrollApi,
): void {
  useEffect(() => {
    stream.forceBottomRef.current = true;
    scroll.setFollowBottom(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);
}

/**
 * 取消闸:上下文变化(切会话/新建/切租户/切数据源)即中止在途流并清残留呈现。
 * 豁免:本流自建会话的 navigate 也会触发 sessionId 变化——该导航
 * 是终态接管的一部分,不得清快照(否则接管间隙以骨架屏闪变复活)。
 */
function useContextReset({ sessionId, current, datasource, stream }: ChatSessionParams): void {
  useEffect(() => {
    const selfNavigated = stream.selfNavigatedRef;
    if (selfNavigated.current !== null && selfNavigated.current === sessionId) {
      selfNavigated.current = null;
      return; // 本流自己的导航:流已终止,呈现交给快照/历史
    }
    selfNavigated.current = null;
    stream.resetStream();
    // 只随上下文键触发；resetStream 内部的 ref/state 不参与判定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, current, datasource]);
}

/** 路由离开页面:释放麦克风 track(泄漏面),不触发状态更新。 */
function useRouteShutdown(stream: ChatStreamApi): void {
  useEffect(() => {
    const s = stream;
    return () => s.shutdown();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/** 终态快照退位:消息历史 refetch 已含该轮(message_id 命中)即撤快照。 */
function useSnapshotRetire(
  stream: ChatStreamApi,
  messagesData: SessionMessageOut[] | undefined,
): void {
  useEffect(() => {
    const snap = stream.finalized;
    if (!snap) return;
    const mid = snap.out.message_id;
    if (mid && messagesData?.some((m) => m.id === mid)) {
      stream.setFinalized(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messagesData, stream.finalized]);
}

interface RecoveryParams {
  sessionId: string | undefined;
  messagesData: SessionMessageOut[] | undefined;
  refetch: () => unknown;
}

/**
 * 有界恢复回查——末条 USER 且无 ASSISTANT(中流刷新/停止接收)
 * 即服务端有轮次在收尾;budget 跨数据变更累计(防每次 refetch 重置变无限轮询),
 * 终态出现或超时即停;离开路由由 cleanup 兜底(无残留轮询)。
 */
function useRecoveryPoll({ sessionId, messagesData, refetch }: RecoveryParams): void {
  const recoveryDeadlineRef = useRef(0);
  useEffect(() => {
    if (!sessionId) return;
    if (!needsTurnRecovery(messagesData)) {
      recoveryDeadlineRef.current = 0;
      return;
    }
    if (recoveryDeadlineRef.current === 0) {
      recoveryDeadlineRef.current = Date.now() + RECOVERY_POLL_BUDGET_MS;
    }
    const timer = window.setInterval(() => {
      refetch();
      if (recoveryExpired(recoveryDeadlineRef.current, Date.now())) {
        window.clearInterval(timer);
        recoveryDeadlineRef.current = 0;
      }
    }, RECOVERY_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [sessionId, messagesData, refetch]);
}
