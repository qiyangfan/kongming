import { describe, expect, it } from "vitest";

import type { SessionMessageOut } from "./api";
import {
  RECOVERY_POLL_BUDGET_MS,
  RECOVERY_POLL_INTERVAL_MS,
  RECOVERY_POLL_MAX,
  recoveryExpired,
  needsTurnRecovery,
} from "./recovery-poll";

/* 有界恢复回查锚:末条 USER 即服务端有轮次收尾(控制面保证 写终态行);回查有界——预算耗尽即停,不无限轮询。 */

function msg(role: "USER" | "ASSISTANT", id: string): SessionMessageOut {
  return {
    id,
    role,
    question: role === "USER" ? "q" : null,
    summary: null,
    sql: null,
    status: "SUCCESS",
    error: null,
    error_detail: null,
    result: null,
    rating: null,
    corrected_sql: null,
    feedback_comment: null,
    attachments: [],
  };
}

describe("needsTurnRecovery", () => {
  it("末条为 USER 且其后无 ASSISTANT → 需回查", () => {
    expect(needsTurnRecovery([msg("USER", "1"), msg("ASSISTANT", "2"), msg("USER", "3")])).toBe(
      true,
    );
  });

  it("末条为 ASSISTANT(含 FAILED 终态)→ 无需回查", () => {
    expect(needsTurnRecovery([msg("USER", "1"), msg("ASSISTANT", "2")])).toBe(false);
    expect(needsTurnRecovery([msg("USER", "1")])).toBe(true);
  });

  it("空/未加载 → 无需回查(冷启动不误触发)", () => {
    expect(needsTurnRecovery(undefined)).toBe(false);
    expect(needsTurnRecovery([])).toBe(false);
  });
});

describe("recoveryExpired", () => {
  it("未在回查(deadline=0)永不过期", () => {
    expect(recoveryExpired(0, Date.now())).toBe(false);
  });

  it("到达/越过 deadline 即过期(超时即停)", () => {
    expect(recoveryExpired(1000, 999)).toBe(false);
    expect(recoveryExpired(1000, 1000)).toBe(true);
    expect(recoveryExpired(1000, 5000)).toBe(true);
  });
});

describe("预算常量", () => {
  it("预算 = 上限次数 × 间隔(30s 有界)", () => {
    expect(RECOVERY_POLL_BUDGET_MS).toBe(RECOVERY_POLL_MAX * RECOVERY_POLL_INTERVAL_MS);
    expect(RECOVERY_POLL_MAX).toBe(15);
  });
});
