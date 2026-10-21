/* 管理套餐、租户配额策略和账单；金额按厘提交，表单展示校验错误。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import {
  bindPlan,
  createPlan,
  deletePlan,
  deletePolicy,
  getTenantPlan,
  listBillingTenants,
  listBillingBots,
  listBillingUsers,
  listBillingTeams,
  listPlans,
  listPolicies,
  pageAdminInvoices,
  updatePlan,
  upsertPolicy,
  voidInvoice,
  type PlanItem,
} from "@/lib/billing-api";
import { DIM_TEXT, PAYMENT_STATUS_TEXT, SCOPE_TEXT } from "@/lib/billing-labels";
import { li2yuan, li2yuanInput } from "@/lib/money";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { PageHeader } from "@/components/page-header";
import { FormRow } from "@/components/form-row";
import { formatNumber } from "@/lib/datetime";

const INVOICE_STATUS_META: StatusMeta = {
  PENDING: { label: "待支付", variant: "secondary" },
  PAID: { label: "已支付", variant: "secondary" },
  VOID: { label: "已作废", variant: "secondary" },
};

/** 限制金额字段，避免超出数据库 INTEGER 范围后落库失败。 */
const INT32_MAX = 2_147_483_647;

/** 数值仅接受非负整数；非法输入返回 null，由表单显示错误。 */
function parseNonNegInt(raw: string): number | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  const n = Number(raw);
  return Number.isSafeInteger(n) && n <= INT32_MAX ? n : null;
}

/** 套餐数值行内校验错误文案(创建/编辑共用) */
const PLAN_NUM_ERROR = "额度须为 0 到 21 亿之间的整数;单价须为非负金额,最多 3 位小数";

/** 单价以「元」录入(精度 0.001 元=1 厘),提交转整数厘;亚厘四舍五入 */
function parseNonNegYuanLi(raw: string): number | null {
  const t = raw.trim();
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const n = Math.round(Number(t) * 1000);
  return Number.isSafeInteger(n) && n <= INT32_MAX ? n : null;
}

const EMPTY_PLAN_FORM = {
  plan_key: "",
  name: "",
  monthly_tokens: "0",
  monthly_calls: "0",
  price_per_mtok: "0",
  price_per_call: "0",
};

export default function BillingPage() {
  const qc = useQueryClient();
  const [tenantId, setTenantIdRaw] = useState("");
  // 切租户连带重置策略表单(防他租户 scope_ref 残留脏态)
  const setTenantId = (v: string) => {
    setTenantIdRaw(v);
    setPolicyForm({
      dimension: "TOKENS",
      limit_value: "0",
      warn_pct: "80",
      scope: "TENANT",
      scope_ref: "",
    });
    setPolicyError("");
  };
  const [planForm, setPlanForm] = useState(EMPTY_PLAN_FORM);
  const [planError, setPlanError] = useState("");
  const [editPlan, setEditPlan] = useState<PlanItem | null>(null);
  const [editForm, setEditForm] = useState(EMPTY_PLAN_FORM);
  const [editError, setEditError] = useState("");
  const [deletePlanTarget, setDeletePlanTarget] = useState<PlanItem | null>(null);
  const [deletePlanError, setDeletePlanError] = useState("");
  const [policyForm, setPolicyForm] = useState({
    dimension: "TOKENS",
    limit_value: "0",
    warn_pct: "80",
    scope: "TENANT",
    scope_ref: "",
  });
  const [policyError, setPolicyError] = useState("");
  const [deletePolicyId, setDeletePolicyId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [voidTarget, setVoidTarget] = useState<string | null>(null);
  const [voidError, setVoidError] = useState("");
  const PAGE_SIZE = 20;

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
    queryFn: () => pageAdminInvoices(page, PAGE_SIZE),
  });
  const tenantPlan = useQuery({
    queryKey: ["admin", "billing", "tenant-plan", tenantId],
    queryFn: () => getTenantPlan(tenantId),
    enabled: tenantId !== "",
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "billing"] });
  };

  const create = useMutation({
    mutationFn: () => {
      const nums = {
        monthly_tokens: parseNonNegInt(planForm.monthly_tokens),
        monthly_calls: parseNonNegInt(planForm.monthly_calls),
        price_per_mtok: parseNonNegYuanLi(planForm.price_per_mtok),
        price_per_call: parseNonNegYuanLi(planForm.price_per_call),
      };
      const values = Object.values(nums);
      if (values.some((n) => n === null)) {
        throw new Error(PLAN_NUM_ERROR);
      }
      // Object.values 丢元组字面量类型,显式解构以保留生成请求类型对字段的检查
      const [monthly_tokens, monthly_calls, price_per_mtok, price_per_call] = values as [
        number,
        number,
        number,
        number,
      ];
      return createPlan({
        plan_key: planForm.plan_key.trim(),
        name: planForm.name.trim(),
        monthly_tokens,
        monthly_calls,
        price_per_mtok,
        price_per_call,
      });
    },
    onSuccess: () => {
      setPlanForm(EMPTY_PLAN_FORM);
      setPlanError("");
      invalidate();
    },
    onError: (e) => setPlanError(e instanceof Error ? e.message : "创建失败"),
  });

  const update = useMutation({
    mutationFn: () => {
      if (!editPlan) throw new Error("无编辑目标");
      const nums = {
        monthly_tokens: parseNonNegInt(editForm.monthly_tokens),
        monthly_calls: parseNonNegInt(editForm.monthly_calls),
        price_per_mtok: parseNonNegYuanLi(editForm.price_per_mtok),
        price_per_call: parseNonNegYuanLi(editForm.price_per_call),
      };
      const values = Object.values(nums);
      if (values.some((n) => n === null)) {
        throw new Error(PLAN_NUM_ERROR);
      }
      const [monthly_tokens, monthly_calls, price_per_mtok, price_per_call] = values as [
        number,
        number,
        number,
        number,
      ];
      return updatePlan(editPlan.id, {
        name: editForm.name.trim(),
        monthly_tokens,
        monthly_calls,
        price_per_mtok,
        price_per_call,
      });
    },
    onSuccess: () => {
      setEditPlan(null);
      setEditError("");
      invalidate();
    },
    onError: (e) => setEditError(e instanceof Error ? e.message : "更新失败"),
  });

  const removePlan = useMutation({
    mutationFn: (id: string) => deletePlan(id),
    onSuccess: () => {
      setDeletePlanTarget(null);
      setDeletePlanError("");
      invalidate();
    },
    onError: (e) => setDeletePlanError(e instanceof Error ? e.message : "删除失败"),
  });

  const upsert = useMutation({
    mutationFn: () => {
      const limit =
        policyForm.dimension === "COST"
          ? parseNonNegYuanLi(policyForm.limit_value)
          : parseNonNegInt(policyForm.limit_value);
      const warn = parseNonNegInt(policyForm.warn_pct);
      if (limit === null || warn === null) {
        throw new Error(
          policyForm.dimension === "COST"
            ? "上限须为非负金额(最多 3 位小数);预警须为 0 到 21 亿的整数"
            : "上限/预警须为 0 到 21 亿的整数",
        );
      }
      return upsertPolicy({
        tenant_id: tenantId,
        dimension: policyForm.dimension,
        limit_value: limit,
        warn_pct: warn,
        scope: policyForm.scope,
        scope_ref: policyForm.scope === "TENANT" ? null : policyForm.scope_ref || null,
      });
    },
    onSuccess: () => {
      setPolicyError("");
      invalidate();
    },
    onError: (e) => setPolicyError(e instanceof Error ? e.message : "保存失败"),
  });

  const removePolicy = useMutation({
    mutationFn: (id: string) => deletePolicy(id, tenantId),
    onSuccess: () => {
      setDeletePolicyId(null);
      invalidate();
    },
    onError: (e) => setPolicyError(e instanceof Error ? e.message : "删除策略失败"),
  });

  const bind = useMutation({
    mutationFn: (planId: string | null) => bindPlan(tenantId, planId),
    onSuccess: () => {
      setPolicyError("");
      invalidate();
    },
    onError: (e) => setPolicyError(e instanceof Error ? e.message : "绑定套餐失败"),
  });

  const voidMut = useMutation({
    mutationFn: (id: string) => voidInvoice(id),
    onSuccess: () => {
      setVoidTarget(null);
      setVoidError("");
      invalidate();
    },
    onError: (e) => setVoidError(e instanceof Error ? e.message : "作废失败"),
  });

  const totalPages = Math.max(1, Math.ceil((invoices.data?.total ?? 0) / PAGE_SIZE));

  return (
    <div className="space-y-5">
      <PageHeader title="计费" description="套餐定价、租户配额策略与全部账单。" />

      <section className="space-y-3 rounded-lg border border-border p-4">
        <h2 className="text-sm font-medium">套餐</h2>
        {plans.isError && <ErrorAlert />}
        <DataView
          rows={plans.data ?? []}
          rowKey={(p) => p.id}
          isLoading={plans.isPending}
          isEmpty={!plans.isPending && (plans.data?.length ?? 0) === 0}
          emptyText="暂无套餐"
          columns={[
            {
              key: "plan_key",
              header: "标识",
              cardPriority: 2,
              render: (p) => <span className="font-mono text-xs">{p.plan_key}</span>,
            },
            {
              key: "name",
              header: "名称",
              cardPriority: 1,
              render: (p) => (
                <span className="block max-w-40 truncate" title={p.name}>
                  {p.name}
                </span>
              ),
            },
            {
              key: "monthly_tokens",
              header: "月 Token",
              cardPriority: 3,
              render: (p) => formatNumber(p.monthly_tokens),
            },
            {
              key: "monthly_calls",
              header: "月调用",
              cardPriority: 4,
              render: (p) => formatNumber(p.monthly_calls),
            },
            {
              key: "price",
              header: "计费单价",
              cardPriority: 5,
              render: (p) => (
                <div className="space-y-0.5">
                  <p>每千 Token ¥{li2yuan(p.price_per_mtok)}</p>
                  <p className="text-muted-foreground">每次调用 ¥{li2yuan(p.price_per_call)}</p>
                </div>
              ),
            },
          ]}
          actions={[
            {
              label: "编辑",
              onSelect: (p) => {
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
              },
            },
            {
              label: "删除",
              destructive: true,
              onSelect: (p) => {
                setDeletePlanTarget(p);
                setDeletePlanError("");
              },
            },
          ]}
        />
        <form
          className="grid grid-cols-2 gap-2 sm:grid-cols-6"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Input
            value={planForm.plan_key}
            onChange={(e) => setPlanForm({ ...planForm, plan_key: e.target.value })}
            placeholder="plan_key"
            aria-label="套餐标识"
            className="font-mono"
          />
          <Input
            value={planForm.name}
            onChange={(e) => setPlanForm({ ...planForm, name: e.target.value })}
            placeholder="名称"
            aria-label="套餐名称"
          />
          <Input
            value={planForm.monthly_tokens}
            onChange={(e) => setPlanForm({ ...planForm, monthly_tokens: e.target.value })}
            placeholder="月 Token(0 不限)"
            aria-label="月 Token"
          />
          <Input
            value={planForm.monthly_calls}
            onChange={(e) => setPlanForm({ ...planForm, monthly_calls: e.target.value })}
            placeholder="月调用(0 不限)"
            aria-label="月调用"
          />
          <Input
            value={planForm.price_per_mtok}
            onChange={(e) => setPlanForm({ ...planForm, price_per_mtok: e.target.value })}
            placeholder="每千 Token 单价(元,如 0.002)"
            aria-label="千 Token 单价"
          />
          <div className="flex gap-2">
            <Input
              value={planForm.price_per_call}
              onChange={(e) => setPlanForm({ ...planForm, price_per_call: e.target.value })}
              placeholder="每次调用单价(元,如 0.001)"
              aria-label="次单价"
            />
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <Plus aria-hidden />
              )}
            </Button>
          </div>
        </form>
        {planError && (
          <p role="alert" className="text-sm text-destructive">
            {planError}
          </p>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-border p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium">租户配额策略</h2>
          <div className="flex items-center gap-2">
            <Select value={tenantId} onValueChange={setTenantId}>
              <SelectTrigger className="w-44" aria-label="选择租户">
                <SelectValue placeholder="选择租户" />
              </SelectTrigger>
              <SelectContent>
                {(tenants.data ?? []).map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.tenant_key}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {tenantId && (
              <Select
                value={tenantPlan.data?.id ?? undefined}
                onValueChange={(v) => bind.mutate(v === "_none" ? null : v)}
              >
                <SelectTrigger className="w-40" aria-label="选择套餐">
                  <SelectValue placeholder="选择套餐…" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="_none">解绑</SelectItem>
                  {(plans.data ?? []).map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
        </div>
        {tenantId && (
          <>
            {policies.isError && <ErrorAlert />}
            <DataView
              rows={policies.data ?? []}
              rowKey={(p) => p.id}
              isLoading={policies.isPending}
              isEmpty={!policies.isPending && (policies.data?.length ?? 0) === 0}
              emptyText="无策略(不限量)"
              columns={[
                {
                  key: "scope",
                  header: "范围",
                  cardPriority: 1,
                  cellClassName: "text-xs",
                  render: (p) => {
                    // USER/TEAM 级以清单数据源回显名称;候选未含(已删除)
                    // 不露 UUID,hover 可得原 id
                    if (p.scope === "BOT") {
                      const botKey = (bots.data ?? []).find((b) => b.id === p.scope_ref)?.bot_key;
                      return botKey ? (
                        <Badge variant="outline">助手:{botKey}</Badge>
                      ) : (
                        <Badge variant="outline" title={p.scope_ref ?? undefined}>
                          未知助手
                        </Badge>
                      );
                    }
                    if (p.scope === "USER") {
                      const username = (users.data ?? []).find(
                        (u) => u.id === p.scope_ref,
                      )?.username;
                      return username ? (
                        <Badge variant="outline">用户:{username}</Badge>
                      ) : (
                        <Badge variant="outline" title={p.scope_ref ?? undefined}>
                          未知用户
                        </Badge>
                      );
                    }
                    if (p.scope === "TEAM") {
                      const teamKey = (teams.data ?? []).find(
                        (t) => t.id === p.scope_ref,
                      )?.team_key;
                      return teamKey ? (
                        <Badge variant="outline">团队:{teamKey}</Badge>
                      ) : (
                        <Badge variant="outline" title={p.scope_ref ?? undefined}>
                          未知团队
                        </Badge>
                      );
                    }
                    return <Badge variant="secondary">租户</Badge>;
                  },
                },
                {
                  key: "dimension",
                  header: "维度",
                  cardPriority: 2,
                  render: (p) => (
                    <Badge variant="secondary">{DIM_TEXT[p.dimension] ?? p.dimension}</Badge>
                  ),
                },
                {
                  key: "limit_value",
                  header: "上限(0=不限)",
                  cardPriority: 3,
                  headClassName: "text-right",
                  cellClassName: "text-right tabular-nums",
                  render: (p) =>
                    p.dimension === "COST"
                      ? `¥${li2yuan(p.limit_value)}`
                      : formatNumber(p.limit_value),
                },
                {
                  key: "warn_pct",
                  header: "预警%",
                  cardPriority: 4,
                  headClassName: "text-right",
                  cellClassName: "text-right tabular-nums",
                  render: (p) => `${p.warn_pct}%`,
                },
              ]}
              actions={[
                {
                  label: "删除",
                  destructive: true,
                  onSelect: (p) => setDeletePolicyId(p.id),
                },
              ]}
            />
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                upsert.mutate();
              }}
            >
              <FormRow label="范围">
                <Select
                  value={policyForm.scope}
                  onValueChange={(v) => setPolicyForm({ ...policyForm, scope: v, scope_ref: "" })}
                >
                  <SelectTrigger className="w-28" aria-label="范围">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="TENANT">{SCOPE_TEXT.TENANT}</SelectItem>
                    <SelectItem value="BOT">{SCOPE_TEXT.BOT}</SelectItem>
                    <SelectItem value="USER">{SCOPE_TEXT.USER}</SelectItem>
                    <SelectItem value="TEAM">{SCOPE_TEXT.TEAM}</SelectItem>
                  </SelectContent>
                </Select>
              </FormRow>
              {policyForm.scope !== "TENANT" && (
                <FormRow label={SCOPE_TEXT[policyForm.scope] ?? policyForm.scope}>
                  <Select
                    value={policyForm.scope_ref}
                    onValueChange={(v) => setPolicyForm({ ...policyForm, scope_ref: v })}
                  >
                    <SelectTrigger
                      className="w-40"
                      aria-label={`选择${SCOPE_TEXT[policyForm.scope] ?? ""}`}
                    >
                      <SelectValue placeholder={`选择${SCOPE_TEXT[policyForm.scope] ?? ""}`} />
                    </SelectTrigger>
                    <SelectContent>
                      {/* 候选项按 scope 取对应清单(bots/users/teams) */}
                      {(policyForm.scope === "BOT"
                        ? (bots.data ?? []).map((b) => ({ id: b.id, label: b.bot_key }))
                        : policyForm.scope === "USER"
                          ? (users.data ?? []).map((u) => ({ id: u.id, label: u.username }))
                          : (teams.data ?? []).map((t) => ({ id: t.id, label: t.team_key }))
                      ).map((o) => (
                        <SelectItem key={o.id} value={o.id}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FormRow>
              )}
              <FormRow label="维度">
                <Select
                  value={policyForm.dimension}
                  onValueChange={(v) =>
                    // 维度切换=单位语义失效(token/次/元),上限清零重录,防跨单位误提交
                    setPolicyForm({ ...policyForm, dimension: v, limit_value: "0" })
                  }
                >
                  <SelectTrigger className="w-32" aria-label="维度">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="TOKENS">{DIM_TEXT.TOKENS}</SelectItem>
                    <SelectItem value="CALLS">{DIM_TEXT.CALLS}</SelectItem>
                    <SelectItem value="COST">{DIM_TEXT.COST}</SelectItem>
                  </SelectContent>
                </Select>
              </FormRow>
              <FormRow label={`上限${policyForm.dimension === "COST" ? "(元)" : ""}`}>
                <Input
                  value={policyForm.limit_value}
                  onChange={(e) => setPolicyForm({ ...policyForm, limit_value: e.target.value })}
                  placeholder={policyForm.dimension === "COST" ? "如 10.00" : "如 100000"}
                  aria-label="上限"
                />
              </FormRow>
              <FormRow label="预警%">
                <Input
                  value={policyForm.warn_pct}
                  onChange={(e) => setPolicyForm({ ...policyForm, warn_pct: e.target.value })}
                  className="w-20"
                  aria-label="预警百分比"
                />
              </FormRow>
              <Button
                type="submit"
                disabled={
                  upsert.isPending ||
                  (policyForm.scope !== "TENANT" && policyForm.scope_ref === "") ||
                  // CALLS 维度仅租户级可用(调用次数不按资源/用户/团队立约)
                  (policyForm.scope !== "TENANT" && policyForm.dimension === "CALLS")
                }
              >
                {upsert.isPending && <Loader2 className="animate-spin" aria-hidden />}
                保存
              </Button>
            </form>
            {policyForm.scope !== "TENANT" && policyForm.dimension === "CALLS" && (
              <p className="text-xs text-muted-foreground" role="note">
                提示:「{DIM_TEXT.CALLS}」维度仅支持「{SCOPE_TEXT.TENANT}
                」范围(调用次数不按资源/用户/团队立约)。
              </p>
            )}
            {policyForm.dimension === "COST" && tenantPlan.data == null && (
              <p className="text-xs text-muted-foreground" role="note">
                提示:该租户未绑定套餐,成本单价按 0 计,「{DIM_TEXT.COST}」上限不会触发限量。
              </p>
            )}
            {policyError && (
              <p role="alert" className="text-sm text-destructive">
                {policyError}
              </p>
            )}
          </>
        )}
      </section>

      <section className="space-y-2 rounded-lg border border-border p-4">
        <h2 className="text-sm font-medium">全部账单</h2>
        {invoices.isError && <ErrorAlert />}
        <DataView
          rows={invoices.data?.items ?? []}
          rowKey={(inv) => inv.id}
          isLoading={invoices.isPending}
          isEmpty={!invoices.isPending && (invoices.data?.items.length ?? 0) === 0}
          emptyText="暂无账单"
          columns={[
            {
              key: "tenant_key",
              header: "租户",
              cardPriority: 1,
              render: (inv) => <span className="font-mono text-xs">{inv.tenant_key}</span>,
            },
            {
              key: "period",
              header: "周期",
              cardPriority: 2,
              render: (inv) => <span className="font-mono text-xs">{inv.period}</span>,
            },
            {
              key: "amount",
              header: "金额(元)",
              cardPriority: 3,
              headClassName: "text-right",
              cellClassName: "text-right tabular-nums",
              render: (inv) => `¥${li2yuan(inv.amount)}`,
            },
            {
              key: "status",
              header: "状态",
              cardPriority: 4,
              render: (inv) => (
                <StatusBadge
                  meta={INVOICE_STATUS_META}
                  value={inv.status}
                  fallbackVariant="secondary"
                />
              ),
            },
            {
              key: "payment_status",
              header: "支付单",
              cellClassName: "text-xs text-muted-foreground",
              render: (inv) =>
                inv.payment_status
                  ? (PAYMENT_STATUS_TEXT[inv.payment_status] ?? inv.payment_status)
                  : "—",
            },
          ]}
          actions={[
            {
              label: "作废",
              // 仅 PENDING 账单可作废(非 PENDING 行不渲染该操作)
              isVisible: (inv) => inv.status === "PENDING",
              onSelect: (inv) => {
                setVoidTarget(inv.id);
                setVoidError("");
              },
            },
          ]}
        />
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            共 {invoices.data?.total ?? 0} 条 · 第 {page}/{totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="xs"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="xs"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </section>

      {/* 套餐编辑对话框 */}
      <Dialog open={editPlan !== null} onOpenChange={(o) => !o && setEditPlan(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>编辑套餐 {editPlan?.plan_key}</DialogTitle>
          </DialogHeader>
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              update.mutate();
            }}
          >
            <FormRow label="名称">
              <Input
                value={editForm.name}
                onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                aria-label="套餐名称"
              />
            </FormRow>
            <div className="grid grid-cols-2 gap-2">
              <FormRow label="月 Token(0 不限)">
                <Input
                  value={editForm.monthly_tokens}
                  onChange={(e) => setEditForm({ ...editForm, monthly_tokens: e.target.value })}
                  aria-label="月 Token"
                />
              </FormRow>
              <FormRow label="月调用(0 不限)">
                <Input
                  value={editForm.monthly_calls}
                  onChange={(e) => setEditForm({ ...editForm, monthly_calls: e.target.value })}
                  aria-label="月调用"
                />
              </FormRow>
              <FormRow label="千 Token 单价(元)">
                <Input
                  value={editForm.price_per_mtok}
                  onChange={(e) => setEditForm({ ...editForm, price_per_mtok: e.target.value })}
                  aria-label="千 Token 单价"
                />
              </FormRow>
              <FormRow label="每次调用单价(元)">
                <Input
                  value={editForm.price_per_call}
                  onChange={(e) => setEditForm({ ...editForm, price_per_call: e.target.value })}
                  aria-label="次单价"
                />
              </FormRow>
            </div>
            {editError && (
              <p role="alert" className="text-sm text-destructive">
                {editError}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              改价只对之后的账单生效;已出账单按出账时的价格结算。
            </p>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending && <Loader2 className="animate-spin" aria-hidden />}
              保存
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      {/* 套餐删除确认:请求进行中禁止关闭 */}
      <AlertDialog
        open={deletePlanTarget !== null}
        onOpenChange={(o) => {
          if (!o && !removePlan.isPending) setDeletePlanTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除套餐 {deletePlanTarget?.plan_key}?</AlertDialogTitle>
            <AlertDialogDescription>
              绑定中的套餐不可删除;删除后新租户不可再绑定,既有账单不受影响。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deletePlanError && (
            <p role="alert" className="text-sm text-destructive">
              {deletePlanError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removePlan.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={removePlan.isPending}
              onClick={(e) => {
                e.preventDefault();
                if (deletePlanTarget) removePlan.mutate(deletePlanTarget.id);
              }}
            >
              {removePlan.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 策略删除确认 */}
      <AlertDialog
        open={deletePolicyId !== null}
        onOpenChange={(o) => {
          if (!o && !removePolicy.isPending) setDeletePolicyId(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除该配额策略?</AlertDialogTitle>
            <AlertDialogDescription>
              删除后该维度不再限量(此操作会记入审计日志)。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removePolicy.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={removePolicy.isPending}
              onClick={(e) => {
                e.preventDefault();
                if (deletePolicyId) removePolicy.mutate(deletePolicyId);
              }}
            >
              {removePolicy.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 账单作废确认 */}
      <AlertDialog
        open={voidTarget !== null}
        onOpenChange={(o) => {
          if (!o && !voidMut.isPending) setVoidTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>作废该账单?</AlertDialogTitle>
            <AlertDialogDescription>
              作废后不可再支付(在途支付单将连带取消);已支付账单不可作废。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {voidError && (
            <p role="alert" className="text-sm text-destructive">
              {voidError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={voidMut.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={voidMut.isPending}
              onClick={(e) => {
                e.preventDefault();
                if (voidTarget) voidMut.mutate(voidTarget);
              }}
            >
              {voidMut.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认作废
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
