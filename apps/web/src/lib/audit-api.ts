/* audit 域 API 封装(015 审计可视化)。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type SqlAuditPageOut = components["schemas"]["SqlAuditPageOut"];
export type SqlAuditItemOut = components["schemas"]["SqlAuditItemOut"];
export type OperLogPageOut = components["schemas"]["OperLogPageOut"];
export type OperLogItemOut = components["schemas"]["OperLogItemOut"];

export interface SqlAuditParams {
  page?: number;
  size?: number;
  trace_id?: string;
  user_id?: string;
  tenant_id?: string;
  verdict?: "PASS" | "BLOCKED" | "ERROR";
  start_time?: string;
  end_time?: string;
}

/** SQL 审计分页(015) */
export function sqlAuditPage(params: SqlAuditParams): Promise<SqlAuditPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  return api.get<SqlAuditPageOut>(`/audit/sql/page${qs.size ? `?${qs}` : ""}`);
}

export interface OperLogParams {
  page?: number;
  size?: number;
  action?: string;
  user_id?: string;
  start_time?: string;
  end_time?: string;
}

/** 操作日志分页(015) */
export function operLogPage(params: OperLogParams): Promise<OperLogPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  return api.get<OperLogPageOut>(`/audit/oper/page${qs.size ? `?${qs}` : ""}`);
}
