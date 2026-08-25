/* toolcall 域 API 封装(019 工具管理与审计)。类型契约由 ADR-005 管线生成。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type ToolListOut = components["schemas"]["ToolListOut"];
export type ToolAuditPageOut = components["schemas"]["ToolAuditPageOut"];

/** 已注册工具清单(只读,system:tool:manage) */
export function toolList(): Promise<ToolListOut> {
  return api.get<ToolListOut>("/system/tools");
}

export interface ToolAuditParams {
  page?: number;
  size?: number;
  tool_name?: string;
  status?: string;
  start?: string;
  end?: string;
}

/** 工具执行审计分页(019,行级可见性:非 ALL 角色服务端强制本人) */
export function toolAuditPage(params: ToolAuditParams): Promise<ToolAuditPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  return api.get<ToolAuditPageOut>(`/system/tools/audit/page${qs.size ? `?${qs}` : ""}`);
}
