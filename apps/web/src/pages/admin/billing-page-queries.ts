/* 计费页的查询与写操作装配:租户/套餐/策略/候选清单/账单,以及各写操作的缓存失效。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  bindPlan,
  deletePlan,
  deletePolicy,
  getTenantPlan,
  listBillingBots,
  listBillingTeams,
  listBillingTenants,
  listBillingUsers,
  listPlans,
  listPolicies,
  pageAdminInvoices,
  voidInvoice,
  type PlanItem,
} from "@/lib/billing-api";
import {
  createPlanFromForm,
  updatePlanFromForm,
  upsertPolicyFromForm,
  type PlanForm,
  type PolicyForm,
} from "./billing-page-model";

export const BILLING_PAGE_SIZE = 20;

export function useBillingQueries(tenantId: string, page: number) {
  const tenants = useQuery({
    queryKey: ["admin", "billing", "tenants"],
    queryFn: listBillingTenants,
  });
  const plans = useQuery({ queryKey: ["admin", "billing", "plans"], queryFn: listPlans });
  const policies = useQuery({
    queryKey: ["admin", "billing", "policies", tenantId],
    queryFn: () => listPolicies(tenantId),
    enabled: tenantId !== "",
  });
  const bots = useQuery({
    queryKey: ["admin", "billing", "bots", tenantId],
    queryFn: () => listBillingBots(tenantId),
    enabled: tenantId !== "",
  });
  // USER/TEAM 级策略选择器数据源(与 Bot 清单同由 billing 域提供)
  const users = useQuery({
    queryKey: ["admin", "billing", "users", tenantId],
    queryFn: () => listBillingUsers(tenantId),
    enabled: tenantId !== "",
  });
  const teams = useQuery({
    queryKey: ["admin", "billing", "teams", tenantId],
    queryFn: () => listBillingTeams(tenantId),
    enabled: tenantId !== "",
  });
  const invoices = useQuery({
    queryKey: ["admin", "billing", "invoices", page],
    queryFn: () => pageAdminInvoices(page, BILLING_PAGE_SIZE),
  });
  const tenantPlan = useQuery({
    queryKey: ["admin", "billing", "tenant-plan", tenantId],
    queryFn: () => getTenantPlan(tenantId),
    enabled: tenantId !== "",
  });
  return { tenants, plans, policies, bots, users, teams, invoices, tenantPlan };
}

// 所有写操作成功后统一失效 billing 前缀缓存
export function useInvalidateBilling() {
  const qc = useQueryClient();
  return () => void qc.invalidateQueries({ queryKey: ["admin", "billing"] });
}

// 写操作的回调契约:成功时清表单/关弹窗并失效缓存,失败时落各自错误位
type BillingHandlers = {
  invalidate: () => void;
  planForm: () => PlanForm;
  editTarget: () => PlanItem | null;
  editForm: () => PlanForm;
  policyForm: () => PolicyForm;
  tenantId: string;
  onPlanCreated: () => void;
  onPlanUpdated: () => void;
  onPlanDeleted: () => void;
  onPolicySaved: () => void;
  onPolicyDeleted: () => void;
  onVoided: () => void;
  onPlanError: (m: string) => void;
  onEditError: (m: string) => void;
  onDeleteError: (m: string) => void;
};

const errText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

export function useBillingMutations(h: BillingHandlers) {
  const policyFailed = (e: unknown, msg: string) => h.onPlanError(errText(e, msg));
  return {
    create: useMutation({
      mutationFn: () => createPlanFromForm(h.planForm()),
      onSuccess: () => {
        h.onPlanCreated();
        h.invalidate();
      },
      onError: (e) => h.onPlanError(errText(e, "创建失败")),
    }),
    update: useMutation({
      mutationFn: () => {
        const target = h.editTarget();
        if (!target) throw new Error("无编辑目标");
        return updatePlanFromForm(target, h.editForm());
      },
      onSuccess: () => {
        h.onPlanUpdated();
        h.invalidate();
      },
      onError: (e) => h.onEditError(errText(e, "更新失败")),
    }),
    removePlan: useMutation({
      mutationFn: (id: string) => deletePlan(id),
      onSuccess: () => {
        h.onPlanDeleted();
        h.invalidate();
      },
      onError: (e) => h.onDeleteError(errText(e, "删除失败")),
    }),
    upsert: useMutation({
      mutationFn: () => upsertPolicyFromForm(h.tenantId, h.policyForm()),
      onSuccess: () => {
        h.onPolicySaved();
        h.invalidate();
      },
      onError: (e) => policyFailed(e, "保存失败"),
    }),
    removePolicy: useMutation({
      mutationFn: (id: string) => deletePolicy(id, h.tenantId),
      onSuccess: () => {
        h.onPolicyDeleted();
        h.invalidate();
      },
      onError: (e) => policyFailed(e, "删除策略失败"),
    }),
    bind: useMutation({
      mutationFn: (planId: string | null) => bindPlan(h.tenantId, planId),
      onSuccess: () => {
        h.onPolicySaved();
        h.invalidate();
      },
      onError: (e) => policyFailed(e, "绑定套餐失败"),
    }),
    voidMut: useMutation({
      mutationFn: (id: string) => voidInvoice(id),
      onSuccess: () => {
        h.onVoided();
        h.invalidate();
      },
      onError: (e) => policyFailed(e, "作废失败"),
    }),
  };
}
