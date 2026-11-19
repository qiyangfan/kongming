/* workflow 域 API 封装；请求和响应类型由 ADR-005 生成。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type WorkflowItem = components["schemas"]["WorkflowDefOut"];
export type WorkflowRunItem = components["schemas"]["WorkflowRunItemOut"];

/** node_runs 契约为自由 JSON(嵌套深),渲染层经本助手窄化(单点)。 */
interface WorkflowNodeRunView {
  node_id: string;
  status: string;
  error?: string;
  duration_ms?: number;
}

export function nodeRunsOf(r: Pick<WorkflowRunItem, "node_runs">): WorkflowNodeRunView[] {
  return (r.node_runs ?? []).map((nr) => ({
    node_id: String(nr.node_id ?? ""),
    status: String(nr.status ?? ""),
    error: typeof nr.error === "string" ? nr.error : undefined,
    duration_ms: typeof nr.duration_ms === "number" ? nr.duration_ms : undefined,
  }));
}

export interface WorkflowRunOut {
  status: string;
  error: string;
  duration_ms: number;
  version: number;
  node_runs: WorkflowRunItem["node_runs"];
}

export function listWorkflows(): Promise<WorkflowItem[]> {
  return api.get<WorkflowItem[]>("/system/workflow");
}

export function saveWorkflow(body: {
  wf_key: string;
  name: string;
  definition: unknown;
}): Promise<{ wf_key: string; version: number }> {
  return api.post("/system/workflow", body);
}

export function setWorkflowEnabled(key: string, enabled: boolean): Promise<{ ok: boolean }> {
  return api.post(`/system/workflow/${key}/${enabled ? "enable" : "disable"}`, {});
}

export function deleteWorkflow(key: string): Promise<{ ok: boolean }> {
  return api.delete(`/system/workflow/${key}`);
}

export function runWorkflow(key: string, inputs: Record<string, unknown>): Promise<WorkflowRunOut> {
  return api.post(`/system/workflow/${key}/run`, { inputs });
}

export function pageWorkflowRuns(
  key: string,
  page: number,
  size: number,
): Promise<{ total: number; items: WorkflowRunItem[] }> {
  return api.get(`/system/workflow/${key}/runs?page=${page}&size=${size}`);
}

/** 获取工作流运行详情,包含列表响应省略的 outputs。 */
export function workflowRunDetail(wfKey: string, runId: string): Promise<WorkflowRunItem> {
  return api.get(`/system/workflow/${wfKey}/runs/${runId}`);
}
