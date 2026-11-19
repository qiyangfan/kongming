/* 管理套餐、租户配额策略和账单；金额按厘提交，表单展示校验错误。 */
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import type { PlanItem } from "@/lib/billing-api";
import { li2yuanInput } from "@/lib/money";
import {
  EMPTY_PLAN_FORM,
  EMPTY_POLICY_FORM,
  type PlanForm,
  type PolicyForm,
} from "./billing-page-model";
import {
  BILLING_PAGE_SIZE,
  useBillingMutations,
  useBillingQueries,
  useInvalidateBilling,
} from "./billing-page-queries";
import { InvoicesSection, PlansSection } from "./billing-page-sections";
import { PoliciesSection } from "./billing-page-policies";
import { BillingDialogs } from "./billing-page-dialogs";

export default function BillingPage() {
  const [tenantId, setTenantIdRaw] = useState("");
  const [planForm, setPlanForm] = useState<PlanForm>(EMPTY_PLAN_FORM);
  const [planError, setPlanError] = useState("");
  const [editPlan, setEditPlan] = useState<PlanItem | null>(null);
  const [editForm, setEditForm] = useState<PlanForm>(EMPTY_PLAN_FORM);
  const [editError, setEditError] = useState("");
  const [deletePlanTarget, setDeletePlanTarget] = useState<PlanItem | null>(null);
  const [deletePlanError, setDeletePlanError] = useState("");
  const [policyForm, setPolicyForm] = useState<PolicyForm>(EMPTY_POLICY_FORM);
  const [policyError, setPolicyError] = useState("");
  const [deletePolicyId, setDeletePolicyId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [voidTarget, setVoidTarget] = useState<string | null>(null);
  const [voidError, setVoidError] = useState("");

  // 切租户连带重置策略表单(防他租户 scope_ref 残留脏态)
  const setTenantId = (v: string) => {
    setTenantIdRaw(v);
    setPolicyForm(EMPTY_POLICY_FORM);
    setPolicyError("");
  };

  const q = useBillingQueries(tenantId, page);
  const invalidate = useInvalidateBilling();

  const m = useBillingMutations({
    invalidate,
    planForm: () => planForm,
    editTarget: () => editPlan,
    editForm: () => editForm,
    policyForm: () => policyForm,
    tenantId,
    onPlanCreated: () => {
      setPlanForm(EMPTY_PLAN_FORM);
      setPlanError("");
    },
    onPlanUpdated: () => {
      setEditPlan(null);
      setEditError("");
    },
    onPlanDeleted: () => {
      setDeletePlanTarget(null);
      setDeletePlanError("");
    },
    onPolicySaved: () => setPolicyError(""),
    onPolicyDeleted: () => setDeletePolicyId(null),
    onVoided: () => {
      setVoidTarget(null);
      setVoidError("");
    },
    onPlanError: setPlanError,
    onEditError: setEditError,
    onDeleteError: setDeletePlanError,
  });
  const { create, upsert, bind } = m;

  const totalPages = Math.max(1, Math.ceil((q.invoices.data?.total ?? 0) / BILLING_PAGE_SIZE));

  const openEdit = (p: PlanItem) => {
    setEditPlan(p);
    setEditForm({
      plan_key: p.plan_key,
      name: p.name,
      monthly_tokens: String(p.monthly_tokens),
      monthly_calls: String(p.monthly_calls),
      price_per_mtok: li2yuanInput(p.price_per_mtok),
      price_per_call: li2yuanInput(p.price_per_call),
    });
    setEditError("");
  };

  return (
    <div className="space-y-5">
      <PageHeader title="计费" description="套餐定价、租户配额策略与全部账单。" />

      <PlansSection
        plans={q.plans}
        planForm={planForm}
        onPlanFormChange={setPlanForm}
        planError={planError}
        creating={create.isPending}
        onCreate={() => create.mutate()}
        onEdit={openEdit}
        onDelete={(p) => {
          setDeletePlanTarget(p);
          setDeletePlanError("");
        }}
      />

      <PoliciesSection
        tenantId={tenantId}
        onTenantIdChange={setTenantId}
        data={q}
        form={policyForm}
        onFormChange={setPolicyForm}
        policyError={policyError}
        savingPending={upsert.isPending}
        bindingPending={bind.isPending}
        onSave={() => upsert.mutate()}
        onBind={(v) => bind.mutate(v)}
        onDelete={setDeletePolicyId}
      />

      <InvoicesSection
        invoices={q.invoices}
        page={page}
        totalPages={totalPages}
        onVoid={(id) => {
          setVoidTarget(id);
          setVoidError("");
        }}
        onPageChange={setPage}
      />

      <BillingDialogs
        m={m}
        edit={{
          plan: editPlan,
          form: editForm,
          onChange: setEditForm,
          error: editError,
          close: () => setEditPlan(null),
        }}
        deletePlan={{
          target: deletePlanTarget,
          error: deletePlanError,
          close: () => setDeletePlanTarget(null),
        }}
        deletePolicyId={deletePolicyId}
        voidError={voidError}
        voidTarget={voidTarget}
        closeDeletePolicy={() => setDeletePolicyId(null)}
        closeVoid={() => setVoidTarget(null)}
      />
    </div>
  );
}
