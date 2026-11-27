/* trigger 域 API 封装；请求和响应类型由 ADR-005 生成。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type TriggerItem = components["schemas"]["TriggerOut"];
export type TriggerRunItem = components["schemas"]["TriggerRunOut"];
export type CronPreviewOut = components["schemas"]["CronPreviewOut"];

export function listTriggers(): Promise<TriggerItem[]> {
  return api.get<TriggerItem[]>("/system/triggers");
}

/** 预览 cron 接下来三次触发时间；非法表达式返回可读错误。 */
export function cronPreview(expr: string): Promise<CronPreviewOut> {
  return api.get(
    `/system/triggers/cron-preview?expr=${encodeURIComponent(expr)}`,
  );
}

export function createTrigger(body: Record<string, unknown>): Promise<{
  id: string;
  webhook_path: string | null;
  secret?: string;
}> {
  return api.post("/system/triggers", body);
}

export function updateTrigger(
  id: string,
  body: Record<string, unknown>,
): Promise<{ ok: boolean }> {
  return api.put(`/system/triggers/${id}`, body);
}

export function setTriggerEnabled(
  id: string,
  enabled: boolean,
): Promise<{ ok: boolean }> {
  return api.post(
    `/system/triggers/${id}/${enabled ? "enable" : "disable"}`,
    {},
  );
}

export function deleteTrigger(id: string): Promise<{ ok: boolean }> {
  return api.delete(`/system/triggers/${id}`);
}

export function pageTriggerRuns(
  id: string,
  page: number,
  size: number,
): Promise<{ total: number; items: TriggerRunItem[] }> {
  return api.get(`/system/triggers/${id}/runs?page=${page}&size=${size}`);
}
