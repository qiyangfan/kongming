import { describe, expect, it } from "vitest";

import type { ChatQueryOut } from "./api";
import {
  snapshotStatus,
  stopNoticeText,
  stopResultFromError,
  stopResultFromResponse,
  type StopNoticeKind,
} from "./turn-stop";

/* 停止的真取消:提示文案随取消结果如实变化;终态快照把 verdict=CANCELLED 映射为已取消状态。 */

function out(p: Partial<ChatQueryOut>): ChatQueryOut {
  return { success: false, verdict: null, summary: "", sql: "", ...p } as ChatQueryOut;
}

describe("snapshotStatus", () => {
  it("verdict=CANCELLED → CANCELLED(与历史回显同形)", () => {
    expect(snapshotStatus(out({ verdict: "CANCELLED" }))).toBe("CANCELLED");
  });

  it("既有四态映射逐字不变", () => {
    expect(snapshotStatus(out({ success: true, verdict: "PASS" }))).toBe("SUCCESS");
    expect(snapshotStatus(out({ verdict: "BLOCKED" }))).toBe("REJECTED");
    expect(snapshotStatus(out({ verdict: "NO_DATA" }))).toBe("NO_DATA");
    expect(snapshotStatus(out({ verdict: "ERROR" }))).toBe("FAILED");
  });
});

describe("取消结果 → 停止提示", () => {
  it("取消成功:说明本轮已取消,且已开始执行的操作不会被撤回", () => {
    const kind = stopResultFromResponse({ outcome: "CANCELLED", message_id: "m1" }).kind;
    const text = stopNoticeText(kind);
    expect(kind).toBe("cancelled");
    expect(text.title).toContain("已取消");
    expect(text.detail).toContain("不会被撤回");
  });

  it("本轮已结束:如实说明已结束,不出现「已取消」", () => {
    const kind = stopResultFromResponse({ outcome: "COMPLETED", message_id: "m1" }).kind;
    const text = stopNoticeText(kind);
    expect(kind).toBe("finished");
    expect(text.title).toContain("已结束");
    expect(`${text.title}${text.detail}`).not.toContain("已取消");
  });

  it("取消请求未能送达:说明仍在后台完成,不声称已取消", () => {
    const kind = stopResultFromError(new Error("网络连接失败,请检查网络后重试")).kind;
    const text = stopNoticeText(kind);
    expect(kind).toBe("not_delivered");
    expect(text.detail).toContain("后台");
    expect(`${text.title}${text.detail}`).not.toContain("已取消");
  });

  it("控制面答 404(已结束且无落库结果):按已结束说明", () => {
    const err = Object.assign(new Error("目标不存在或已被删除"), { status: 404 });
    expect(stopResultFromError(err).kind).toBe("finished");
  });

  it("流尚未开始(标识未下发):按断线续存如实说明", () => {
    const text = stopNoticeText("not_started");
    expect(text.detail).toContain("后台");
    expect(`${text.title}${text.detail}`).not.toContain("已取消");
  });

  it("等待取消结果期间只说「正在取消」,不提前声称已取消", () => {
    const text = stopNoticeText("pending");
    expect(text.title).toContain("正在");
    expect(`${text.title}${text.detail}`).not.toMatch(/已取消/);
  });
});

describe("每种停止结果都有文案", () => {
  const kinds: StopNoticeKind[] = [
    "pending",
    "cancelled",
    "finished",
    "not_delivered",
    "not_started",
  ];
  it("标题均非空", () => {
    for (const k of kinds) expect(stopNoticeText(k).title.length).toBeGreaterThan(0);
  });
});
