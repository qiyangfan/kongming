/* guardrail 域 API 封装( prompt 安全护栏)。类型契约由 ADR-005 管线生成。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

type RuleListOut = components["schemas"]["RuleListOut"];
type GuardrailAuditPageOut = components["schemas"]["GuardrailAuditPageOut"];

export function guardrailRules(): Promise<RuleListOut> {
  return api.get<RuleListOut>("/system/guardrails/rules");
}

export function updateGuardrailRule(
  name: string,
  body: {
    enabled: boolean;
    action?: "blocked" | "masked" | null;
    threshold?: number | null;
  },
): Promise<void> {
  return api.put<void>(`/system/guardrails/rules/${name}`, body);
}

interface GuardrailAuditParams {
  page?: number;
  size?: number;
  guardrail_type?: "input" | "output" | "tool_intent";
  rule_name?: string;
  action?: "blocked" | "masked";
  start?: string;
  end?: string;
}

export function guardrailAuditPage(
  params: GuardrailAuditParams,
): Promise<GuardrailAuditPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  const suffix = qs.size > 0 ? `?${qs}` : "";
  return api.get<GuardrailAuditPageOut>(
    `/system/guardrails/audit/page${suffix}`,
  );
}
