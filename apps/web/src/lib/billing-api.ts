/* billing 域 API 封装:支付流程、套餐增删改查、租户/用户/团队/Bot 清单、策略绑定与账单分页。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type PortalUsage = components["schemas"]["PortalUsageOut"];
export type InvoiceItem = components["schemas"]["InvoiceOut"];
export type InvoiceDetail = components["schemas"]["InvoiceDetailOut"];
export type PlanItem = components["schemas"]["PlanOut"];
export type PolicyItem = components["schemas"]["PolicyOut"];
export type PortalKeyItem = components["schemas"]["PortalKeyOut"];
export type PaymentItem = components["schemas"]["PaymentOut"];
type ReconcileResult = components["schemas"]["ReconcileOut"];
export type TenantBrief = components["schemas"]["TenantBriefOut"];
export type InvoiceAdminPage = components["schemas"]["InvoiceAdminPageOut"];

export function getPortalUsage(): Promise<PortalUsage> {
  return api.get<PortalUsage>("/portal/usage");
}

export function listPortalInvoices(): Promise<InvoiceItem[]> {
  return api.get<InvoiceItem[]>("/portal/invoices");
}

export function getInvoiceDetail(id: string): Promise<InvoiceDetail> {
  return api.get<InvoiceDetail>(`/portal/invoices/${id}`);
}

/* ── 门户支付 ── */

export function createPayment(invoiceId: string): Promise<PaymentItem> {
  return api.post(`/portal/invoices/${invoiceId}/payments`, undefined);
}

export function getPayment(paymentId: string): Promise<PaymentItem> {
  return api.get<PaymentItem>(`/portal/payments/${paymentId}`);
}

export function cancelPayment(paymentId: string): Promise<{ ok: boolean }> {
  return api.post(`/portal/payments/${paymentId}/cancel`, undefined);
}

export function mockSimulate(
  paymentId: string,
  result: "SUCCESS" | "FAILURE",
): Promise<ReconcileResult> {
  return api.post(`/portal/payments/${paymentId}/mock-simulate`, { result });
}

/* ── 超管配置 ── */

export function listPortalKeys(): Promise<PortalKeyItem[]> {
  return api.get<PortalKeyItem[]>("/portal/keys");
}

export function listPlans(): Promise<PlanItem[]> {
  return api.get<PlanItem[]>("/billing/plans");
}

export function createPlan(body: components["schemas"]["PlanIn"]): Promise<{ id: string }> {
  return api.post("/billing/plans", body);
}

export function updatePlan(
  id: string,
  body: components["schemas"]["PlanPatchIn"],
): Promise<{ ok: boolean }> {
  return api.patch(`/billing/plans/${id}`, body);
}

export function deletePlan(id: string): Promise<{ ok: boolean }> {
  return api.delete(`/billing/plans/${id}`);
}

export function listBillingTenants(): Promise<TenantBrief[]> {
  return api.get<TenantBrief[]>("/billing/tenants");
}

export type BotBrief = components["schemas"]["BotBriefOut"];

export function listBillingBots(tenantId: string): Promise<BotBrief[]> {
  return api.get<BotBrief[]>(`/billing/bots?tenant_id=${tenantId}`);
}

export type UserBrief = components["schemas"]["UserBriefOut"];
export type TeamBrief = components["schemas"]["TeamBriefOut"];

/** USER/TEAM 级策略选择器的候选清单(与 Bot 清单同由 billing 域提供) */
export function listBillingUsers(tenantId: string): Promise<UserBrief[]> {
  return api.get<UserBrief[]>(`/billing/users?tenant_id=${tenantId}`);
}

export function listBillingTeams(tenantId: string): Promise<TeamBrief[]> {
  return api.get<TeamBrief[]>(`/billing/teams?tenant_id=${tenantId}`);
}

export function listPolicies(tenantId: string): Promise<PolicyItem[]> {
  return api.get<PolicyItem[]>(`/billing/policies?tenant_id=${tenantId}`);
}

export function upsertPolicy(body: Record<string, unknown>): Promise<{ ok: boolean }> {
  return api.post("/billing/policies", body);
}

export function deletePolicy(id: string, tenantId: string): Promise<{ ok: boolean }> {
  return api.delete(`/billing/policies/${id}?tenant_id=${tenantId}`);
}

export function getTenantPlan(tenantId: string): Promise<PlanItem | null> {
  return api.get<PlanItem | null>(`/billing/tenants/${tenantId}/plan`);
}

export function bindPlan(tenantId: string, planId: string | null): Promise<{ ok: boolean }> {
  return api.post("/billing/tenants/bind-plan", { tenant_id: tenantId, plan_id: planId });
}

export function pageAdminInvoices(page: number, size: number): Promise<InvoiceAdminPage> {
  return api.get<InvoiceAdminPage>(`/billing/invoices?page=${page}&size=${size}`);
}

export function voidInvoice(invoiceId: string): Promise<{ ok: boolean }> {
  return api.post(`/billing/invoices/${invoiceId}/void`, undefined);
}
