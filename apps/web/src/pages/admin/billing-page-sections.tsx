/* 计费页三个区块:套餐列表与新建、租户配额策略、全部账单。 */

import type { SubmitEvent } from "react";
import { Loader2, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { StatusBadge } from "@/components/status-badge";
import type { InvoiceAdminPage, PlanItem } from "@/lib/billing-api";
import { PAYMENT_STATUS_TEXT } from "@/lib/billing-labels";
import { li2yuan } from "@/lib/money";
import { formatNumber } from "@/lib/datetime";
import { INVOICE_STATUS_META, type PlanForm } from "./billing-page-model";

export type QueryLike<T> = { data: T | undefined; isPending: boolean; isError: boolean };

const preventDefault = (e: SubmitEvent<HTMLFormElement>) => e.preventDefault();

export function PlansSection({
  plans,
  planForm,
  onPlanFormChange,
  planError,
  creating,
  onCreate,
  onEdit,
  onDelete,
}: {
  plans: QueryLike<PlanItem[]>;
  planForm: PlanForm;
  onPlanFormChange: (f: PlanForm) => void;
  planError: string;
  creating: boolean;
  onCreate: () => void;
  onEdit: (p: PlanItem) => void;
  onDelete: (p: PlanItem) => void;
}) {
  const rows = plans.data ?? [];
  return (
    <section className="space-y-3 rounded-lg border border-border p-4">
      <h2 className="text-sm font-medium">套餐</h2>
      {plans.isError && <ErrorAlert />}
      <DataView
        rows={rows}
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
          { label: "编辑", onSelect: onEdit },
          { label: "删除", destructive: true, onSelect: onDelete },
        ]}
      />
      <form
        className="grid grid-cols-2 gap-2 sm:grid-cols-6"
        onSubmit={(e) => {
          preventDefault(e);
          onCreate();
        }}
      >
        <Input
          value={planForm.plan_key}
          onChange={(e) => onPlanFormChange({ ...planForm, plan_key: e.target.value })}
          placeholder="plan_key"
          aria-label="套餐标识"
          className="font-mono"
        />
        <Input
          value={planForm.name}
          onChange={(e) => onPlanFormChange({ ...planForm, name: e.target.value })}
          placeholder="名称"
          aria-label="套餐名称"
        />
        <Input
          value={planForm.monthly_tokens}
          onChange={(e) => onPlanFormChange({ ...planForm, monthly_tokens: e.target.value })}
          placeholder="月 Token(0 不限)"
          aria-label="月 Token"
        />
        <Input
          value={planForm.monthly_calls}
          onChange={(e) => onPlanFormChange({ ...planForm, monthly_calls: e.target.value })}
          placeholder="月调用(0 不限)"
          aria-label="月调用"
        />
        <Input
          value={planForm.price_per_mtok}
          onChange={(e) => onPlanFormChange({ ...planForm, price_per_mtok: e.target.value })}
          placeholder="每千 Token 单价(元,如 0.002)"
          aria-label="千 Token 单价"
        />
        <div className="flex gap-2">
          <Input
            value={planForm.price_per_call}
            onChange={(e) => onPlanFormChange({ ...planForm, price_per_call: e.target.value })}
            placeholder="每次调用单价(元,如 0.001)"
            aria-label="次单价"
          />
          <Button type="submit" disabled={creating}>
            {creating ? <Loader2 className="animate-spin" aria-hidden /> : <Plus aria-hidden />}
          </Button>
        </div>
      </form>
      {planError && (
        <p role="alert" className="text-sm text-destructive">
          {planError}
        </p>
      )}
    </section>
  );
}

export function InvoicesSection({
  invoices,
  page,
  totalPages,
  onVoid,
  onPageChange,
}: {
  invoices: QueryLike<InvoiceAdminPage>;
  page: number;
  totalPages: number;
  onVoid: (id: string) => void;
  onPageChange: (page: number) => void;
}) {
  return (
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
            onSelect: (inv) => onVoid(inv.id),
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
            onClick={() => onPageChange(page - 1)}
          >
            上一页
          </Button>
          <Button
            variant="outline"
            size="xs"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
    </section>
  );
}
