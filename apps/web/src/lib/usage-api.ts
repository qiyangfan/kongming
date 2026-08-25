/* 用量观测域 API 封装(038 agent-observability)。类型契约由 ADR-005
   管线生成(禁手写);汇总/趋势/明细/时间线四端点同挂
   system:llm-usage:view。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type UsageSummaryOut = components["schemas"]["UsageSummaryOut"];
export type UsageSummaryItem = components["schemas"]["UsageSummaryItem"];
export type UsageTrendOut = components["schemas"]["UsageTrendOut"];
export type UsagePageOut = components["schemas"]["UsagePageOut"];
export type UsageRow = components["schemas"]["UsageRow"];
export type UsageTimelineOut = components["schemas"]["UsageTimelineOut"];
export type TimelineEvent = components["schemas"]["TimelineEvent"];

/** 汇总(租户+模型+任务;时间范围可选) */
export function usageSummary(params: {
  start?: string;
  end?: string;
} = {}): Promise<UsageSummaryOut> {
  const qs = new URLSearchParams();
  if (params.start) qs.set("start", params.start);
  if (params.end) qs.set("end", params.end);
  const suffix = qs.size > 0 ? `?${qs}` : "";
  return api.get<UsageSummaryOut>(`/system/llm-usage/summary${suffix}`);
}

/** 按天趋势(Asia/Shanghai 日历日,含今天;逐天补零) */
export function usageTrend(days: number): Promise<UsageTrendOut> {
  return api.get<UsageTrendOut>(`/system/llm-usage/trend?days=${days}`);
}

export interface UsagePageParams {
  page?: number;
  size?: number;
  model?: string;
  task?: string;
  status?: "SUCCESS" | "ERROR";
  trace_id?: string;
}

/** 明细分页(created_at 倒序;四类筛选) */
export function usagePage(params: UsagePageParams): Promise<UsagePageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  const suffix = qs.size > 0 ? `?${qs}` : "";
  return api.get<UsagePageOut>(`/system/llm-usage/page${suffix}`);
}

/** trace 时间线(LLM+TOOL+SQL 三路事件流) */
export function usageTimeline(traceId: string): Promise<UsageTimelineOut> {
  return api.get<UsageTimelineOut>(
    `/system/llm-usage/timeline?trace_id=${encodeURIComponent(traceId)}`,
  );
}
