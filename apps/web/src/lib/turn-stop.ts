/* 问答轮的停止:取消结果的归类与提示文案;终态快照的状态映射。 */

import type { ChatQueryOut, SessionMessageOut, TurnCancelOut } from "./api";

/* 终态快照的状态码:失败按 verdict 区分安全护栏拦截、知识未覆盖与已取消。 */
export function snapshotStatus(out: ChatQueryOut): SessionMessageOut["status"] {
  if (out.success) return "SUCCESS";
  if (out.verdict === "BLOCKED") return "REJECTED";
  if (out.verdict === "NO_DATA") return "NO_DATA";
  if (out.verdict === "CANCELLED") return "CANCELLED";
  return "FAILED";
}

/** 停止后提示的依据(文案随它如实变化) */
export type StopNoticeKind =
  | "pending" // 取消请求在途
  | "cancelled" // 控制面确认本轮已取消
  | "finished" // 取消到达前本轮已结束(含已结束且没有落库结果)
  | "not_delivered" // 取消请求未能送达控制面
  | "not_started"; // 响应头尚未返回、本轮标识未知,未发起取消

export interface StopNotice {
  kind: StopNoticeKind;
}

/** 取消端点的应答 → 停止提示依据 */
export function stopResultFromResponse(body: TurnCancelOut): StopNotice {
  return { kind: body.outcome === "CANCELLED" ? "cancelled" : "finished" };
}

/** 取消请求失败 → 停止提示依据:404 为本轮已结束且无落库结果,其余(网络/超时/5xx)为未能送达 */
export function stopResultFromError(err: unknown): StopNotice {
  const status = err instanceof Error ? (err as { status?: number }).status : undefined;
  return { kind: status === 404 ? "finished" : "not_delivered" };
}

const STOP_NOTICE_TEXT: Record<StopNoticeKind, { title: string; detail: string }> = {
  pending: {
    title: "正在请求取消本轮…",
    detail: "收到结果后会在这里说明。",
  },
  cancelled: {
    title: "本轮已取消。",
    detail: "已开始执行的操作不会被撤回;问句已回到输入框,可立即重新提问。",
  },
  finished: {
    title: "本轮已结束,没能取消。",
    detail: "如果这一轮保存过结果,可以在对话记录里查看。",
  },
  not_delivered: {
    title: "取消请求没能送达。",
    detail: "本轮仍会在后台完成,可稍后在对话记录里回看;问句已回到输入框。",
  },
  not_started: {
    title: "已停止接收本轮输出。",
    detail: "本轮若已在后台开始,会照常完成,可稍后回看;问句已回到输入框,也可立即重发。",
  },
};

/** 停止提示文案(按取消结果) */
export function stopNoticeText(kind: StopNoticeKind): { title: string; detail: string } {
  return STOP_NOTICE_TEXT[kind];
}
