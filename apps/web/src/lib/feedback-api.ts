/* 反馈 API 封装；请求与响应类型由 ADR-005 契约生成。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

type FeedbackIn = components["schemas"]["FeedbackIn"];
type HandleIn = components["schemas"]["HandleIn"];
export type FeedbackOut = components["schemas"]["FeedbackOut"];
type HandleOut = components["schemas"]["HandleOut"];
type InvolvedTablesOut = components["schemas"]["InvolvedTablesOut"];
type OverviewOut = components["schemas"]["OverviewOut"];
type FeedbackPageOut = components["schemas"]["FeedbackPageOut"];
export type FeedbackPageItemOut = components["schemas"]["FeedbackPageItemOut"];

/** 反馈提交:rating 必传;comment/corrected_sql null=保持现值,空串=清空 */
export function submitFeedback(messageId: string, payload: FeedbackIn): Promise<FeedbackOut> {
  return api.post<FeedbackOut>(`/chat/messages/${messageId}/feedback`, payload);
}

/** 处置(feedback-refine):status 四态;note null=保持,空串=清空 */
export function handleFeedback(messageId: string, payload: HandleIn): Promise<HandleOut> {
  return api.put<HandleOut>(`/feedback/${messageId}/status`, payload);
}

/** 涉及表定位(feedback-refine) */
export function involvedTables(messageId: string): Promise<InvolvedTablesOut> {
  return api.get<InvolvedTablesOut>(`/feedback/${messageId}/involved-tables`);
}

/** 统计概览(feedback-analytics) */
export function feedbackOverview(params: {
  days?: number;
  tenant_id?: string;
  datasource_key?: string;
}): Promise<OverviewOut> {
  const qs = new URLSearchParams();
  if (params.days !== undefined) qs.set("days", String(params.days));
  if (params.tenant_id) qs.set("tenant_id", params.tenant_id);
  if (params.datasource_key) qs.set("datasource_key", params.datasource_key);
  const suffix = qs.size > 0 ? `?${qs}` : "";
  return api.get<OverviewOut>(`/feedback/overview${suffix}`);
}

interface FeedbackPageParams {
  page?: number;
  size?: number;
  user_id?: string;
  rating?: "UP" | "DOWN";
  tenant_id?: string;
  datasource_key?: string;
  feedback_status?: "PENDING" | "ADOPTED" | "REJECTED" | "DISMISSED";
  only_corrected?: boolean;
  start_time?: string;
  end_time?: string;
}

/** 明细分页(feedback-analytics) */
export function feedbackPage(params: FeedbackPageParams): Promise<FeedbackPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  const suffix = qs.size > 0 ? `?${qs}` : "";
  return api.get<FeedbackPageOut>(`/feedback/page${suffix}`);
}
