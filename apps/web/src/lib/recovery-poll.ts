/** 有界恢复回查(078 任务 3):控制面对每条链路保证写终态行(含 agent 流无
 *  终态的汇聚分支),故"末条为 USER"必然意味着有一轮正在服务端收尾——中流
 *  刷新/停止接收后,以有界节奏回查历史直至终态出现或预算耗尽(不无限轮询)。 */

import type { SessionMessageOut } from "./api";

export const RECOVERY_POLL_INTERVAL_MS = 2000;
/** 回查总预算(上限次数 × 间隔 = 30s):超时即停,终态未现由用户切走再回来自然刷新 */
export const RECOVERY_POLL_MAX = 15;
export const RECOVERY_POLL_BUDGET_MS = RECOVERY_POLL_INTERVAL_MS * RECOVERY_POLL_MAX;

/** 末条为 USER 且其后无 ASSISTANT → 需要对历史做回查(服务端有轮次在收尾)。 */
export function needsTurnRecovery(msgs: SessionMessageOut[] | undefined): boolean {
  if (!msgs || msgs.length === 0) return false;
  return msgs[msgs.length - 1].role === "USER";
}

/** 回查预算是否已耗尽(deadline  epoch ms;0 = 未在回查)。 */
export function recoveryExpired(deadline: number, now: number): boolean {
  return deadline > 0 && now >= deadline;
}
