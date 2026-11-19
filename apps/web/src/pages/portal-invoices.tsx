/* 门户「账单」区与账单明细对话框。 */

import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { INVOICE_STATUS_TEXT } from "@/lib/billing-labels";
import type { InvoiceDetail, InvoiceItem } from "@/lib/billing-api";
import { li2yuan } from "@/lib/money";
import { formatNumber } from "@/lib/datetime";

// 发票状态 → StatusBadge;未知值回落 outline,
// label 与 lib/billing-labels 的 INVOICE_STATUS_TEXT 对齐
const INVOICE_STATUS_META: StatusMeta = {
  PENDING: { label: "待支付", variant: "secondary" },
  PAID: { label: "已支付", variant: "outline" },
  VOID: { label: "已作废", variant: "outline" },
};

/** 发票明细 usage_json 的固定结构;生成类型为宽泛 object,此处本地收窄,
 * 字段与服务端组装处一一对应。 */
interface UsageDetail {
  by_model?: { model: string; prompt: number; completion: number; calls: number }[];
  total_tokens?: number;
  total_calls?: number;
  price_per_mtok?: number;
  price_per_call?: number;
}

/** 从发票明细取用量结构。 */
function usageOf(detail: InvoiceDetail): UsageDetail {
  return detail.usage_json as unknown as UsageDetail;
}

/** 单行费用(整数厘),与服务端 estimate_cost 同式(整数运算、向下取整)。
 * 服务端总额按合计一次计算,各行求和与总额至多差尾数取整(厘级),总额以 amount 为准。 */
function rowCostLi(
  row: { prompt: number; completion: number; calls: number },
  u: UsageDetail,
): number {
  return (
    Math.floor(((row.prompt + row.completion) * (u.price_per_mtok ?? 0)) / 1000) +
    row.calls * (u.price_per_call ?? 0)
  );
}

/** 将单价代入计费公式,并按计费项换行,避免窄屏在价格和运算符中间折行。 */
function formulaLines(u: UsageDetail): string[] {
  const tokens = u.total_tokens ?? 0;
  const calls = u.total_calls ?? 0;
  const tokenFee = Math.floor((tokens * (u.price_per_mtok ?? 0)) / 1000);
  return [
    "按用量计费:",
    `Token ${formatNumber(tokens)} × ¥${li2yuan(u.price_per_mtok ?? 0)}/千 = ¥${li2yuan(tokenFee)}`,
    `调用 ${formatNumber(calls)} 次 × ¥${li2yuan(u.price_per_call ?? 0)} = ¥${li2yuan(calls * (u.price_per_call ?? 0))}`,
  ];
}

type ModelUsageRow = NonNullable<UsageDetail["by_model"]>[number];

const MODEL_COLUMNS = (detail: InvoiceDetail) => [
  {
    key: "model",
    header: "模型",
    cardPriority: 1,
    render: (m: ModelUsageRow) => (
      <span className="block max-w-40 truncate font-mono text-xs" title={m.model}>
        {m.model}
      </span>
    ),
  },
  {
    key: "prompt",
    header: "输入 Token",
    render: (m: ModelUsageRow) => formatNumber(m.prompt),
  },
  {
    key: "completion",
    header: "输出 Token",
    render: (m: ModelUsageRow) => formatNumber(m.completion),
  },
  { key: "calls", header: "调用次数", render: (m: ModelUsageRow) => formatNumber(m.calls) },
  {
    key: "cost",
    header: "费用",
    headClassName: "text-right",
    cellClassName: "text-right",
    render: (m: ModelUsageRow) => `¥${li2yuan(rowCostLi(m, usageOf(detail)))}`,
  },
];

/** 按模型拆分的用量表(费用列按行计费式重算)。 */
function ModelUsageTable({ detail }: { detail: InvoiceDetail }) {
  const rows = usageOf(detail).by_model ?? [];
  if (rows.length === 0) return null;
  return <DataView rows={rows} rowKey={(m) => m.model} columns={MODEL_COLUMNS(detail)} />;
}

/** 账单明细正文:金额与状态、按模型用量、计费公式。 */
export function InvoiceDetailBody({ detail }: { detail: InvoiceDetail }) {
  return (
    <div className="space-y-3 text-sm">
      <p>
        金额:<strong>¥{li2yuan(detail.amount)}</strong> · 状态:
        {INVOICE_STATUS_TEXT[detail.status] ?? detail.status}
      </p>
      <ModelUsageTable detail={detail} />
      <div className="space-y-1 text-xs text-muted-foreground">
        {formulaLines(usageOf(detail)).map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
    </div>
  );
}

/** 待支付行内的「去支付」按钮;DataViewAction 无按行条件,故挂在状态列。 */
function PayNowButton({
  inv,
  payingId,
  onPay,
}: {
  inv: InvoiceItem;
  payingId: string | null;
  onPay: (id: string) => void;
}) {
  if (inv.status !== "PENDING") return null;
  return (
    <Button
      variant="outline"
      size="xs"
      disabled={payingId === inv.id}
      onClick={() => onPay(inv.id)}
    >
      {payingId === inv.id && <Loader2 className="animate-spin" aria-hidden />}
      去支付
    </Button>
  );
}

interface InvoicesSectionProps {
  invoices: InvoiceItem[] | undefined;
  isPending: boolean;
  isError: boolean;
  payingId: string | null;
  payError: string;
  onDetail: (id: string) => void;
  onPay: (id: string) => void;
}

/** 账单列表:周期/金额/状态,待支付行内可直接去支付。 */
export function InvoicesSection(props: InvoicesSectionProps) {
  const { invoices, isPending, payingId, payError } = props;
  return (
    <section className="space-y-2">
      <h2 className="text-sm font-medium">账单</h2>
      {props.isError && <ErrorAlert />}
      {/* 外框仅在宽屏保留(md- 卡片形态裸露,与 DataView 样板口径一致) */}
      <div className="md:rounded-lg md:border">
        <DataView
          rows={invoices ?? []}
          rowKey={(inv) => inv.id}
          isLoading={isPending}
          isEmpty={!isPending && invoices?.length === 0}
          emptyText="暂无账单(每月 1 日出账)。"
          columns={[
            {
              key: "period",
              header: "周期",
              cardPriority: 1,
              render: (inv) => <span className="font-mono text-xs">{inv.period}</span>,
            },
            {
              key: "amount",
              header: "金额(元)",
              headClassName: "text-right",
              cellClassName: "text-right tabular-nums",
              cardPriority: 2,
              render: (inv) => `¥${li2yuan(inv.amount)}`,
            },
            {
              key: "status",
              header: "状态",
              cardPriority: 3,
              render: (inv) => (
                <div className="flex items-center gap-2">
                  <StatusBadge meta={INVOICE_STATUS_META} value={inv.status} />
                  <PayNowButton inv={inv} payingId={payingId} onPay={props.onPay} />
                </div>
              ),
            },
          ]}
          actions={[{ label: "明细", onSelect: (inv) => props.onDetail(inv.id) }]}
        />
      </div>
      {payError && (
        <p role="alert" className="text-sm text-destructive">
          {payError}
        </p>
      )}
    </section>
  );
}
