/* 用量页的格式化与状态映射纯函数。 */

import { Database, Sparkles, Wrench } from "lucide-react";

import type { StatusMeta } from "@kongming/ui/status-badge";

export function fmtTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 10_000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

export const KIND_ICON = { LLM: Sparkles, TOOL: Wrench, SQL: Database } as const;

// 时间线事件类型的中文展示;未收录值回退原值
const KIND_LABELS: Record<string, string> = {
  LLM: "模型调用",
  TOOL: "工具",
  SQL: "SQL",
};

const TOOL_STATUS_LABEL: Record<string, string> = {
  EXECUTED: "已执行",
  REJECTED: "已拒绝",
  ERROR: "错误",
  RATE_LIMITED: "限流",
  PENDING_CONFIRM: "待确认",
  CONFIRM_REJECTED: "确认拒绝",
  CONFIRM_EXPIRED: "确认过期",
};

// SQL verdict 与 LLM status 的中文映射(与 TOOL 状态表同一族)
const VERDICT_LABEL: Record<string, string> = {
  PASS: "通过",
  BLOCKED: "拦截",
  ERROR: "错误",
};
const LLM_STATUS_LABEL: Record<string, string> = {
  SUCCESS: "成功",
  ERROR: "失败",
};

// 明细表状态列(值 → 徽章统一走 StatusBadge)
export const LLM_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  ERROR: { label: "失败", variant: "destructive" },
};

export function kindStatusLabel(kind: string, status: string): string {
  let table: Record<string, string> = LLM_STATUS_LABEL;
  if (kind === "TOOL") table = TOOL_STATUS_LABEL;
  else if (kind === "SQL") table = VERDICT_LABEL;
  return table[status] ?? status;
}

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

/** 时间线事件是否为失败态(决定图标色与徽章变体)。 */
export function isErrorStatus(status: string): boolean {
  return (
    status === "ERROR" ||
    status === "REJECTED" ||
    status === "RATE_LIMITED" ||
    status === "CONFIRM_REJECTED" ||
    status === "CONFIRM_EXPIRED" ||
    status === "BLOCKED"
  );
}
