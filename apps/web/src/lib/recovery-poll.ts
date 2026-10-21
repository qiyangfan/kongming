/** 页面恢复时，如果末条消息仍是 USER，就按固定预算回查，直到服务端补齐终态或预算耗尽。 */

import type { SessionMessageOut } from "./api";

export const RECOVERY_POLL_INTERVAL_MS = 2000;
/** 回查总时长为 30 秒；到期后停止轮询。 */
export const RECOVERY_POLL_MAX = 15;
export const RECOVERY_POLL_BUDGET_MS = RECOVERY_POLL_INTERVAL_MS * RECOVERY_POLL_MAX;

/** 末条为 USER 且其后无 ASSISTANT → 需要对历史做回查(服务端有轮次在收尾)。 */
export function needsTurnRecovery(msgs: SessionMessageOut[] | undefined): boolean {
  if (!msgs || msgs.length === 0) return false;
  return msgs[msgs.length - 1].role === "USER";
}

/** 回查预算是否已耗尽(deadline epoch ms;0 = 未在回查)。 */
export function recoveryExpired(deadline: number, now: number): boolean {
  return deadline > 0 && now >= deadline;
}
