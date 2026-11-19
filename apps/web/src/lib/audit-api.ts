/* audit 域 API 封装。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

type SqlAuditPageOut = components["schemas"]["SqlAuditPageOut"];
type OperLogPageOut = components["schemas"]["OperLogPageOut"];

interface SqlAuditParams {
  page?: number;
  size?: number;
  trace_id?: string;
  user_id?: string;
  tenant_id?: string;
  verdict?: "PASS" | "BLOCKED" | "ERROR";
  start_time?: string;
  end_time?: string;
}

/** SQL 审计分页 */
export function sqlAuditPage(params: SqlAuditParams): Promise<SqlAuditPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  return api.get<SqlAuditPageOut>(`/audit/sql/page${qs.size ? `?${qs}` : ""}`);
}

interface OperLogParams {
  page?: number;
  size?: number;
  action?: string;
  user_id?: string;
  start_time?: string;
  end_time?: string;
}

/** 操作日志分页 */
export function operLogPage(params: OperLogParams): Promise<OperLogPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  return api.get<OperLogPageOut>(`/audit/oper/page${qs.size ? `?${qs}` : ""}`);
}
