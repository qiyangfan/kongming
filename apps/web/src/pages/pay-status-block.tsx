/* 收银台按支付单状态呈现的主体:待支付的模拟码与操作、各终态结果说明。 */

import { AlertTriangle, CheckCircle2, Loader2, QrCode, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { InvoiceDetail, PaymentItem } from "@/lib/billing-api";
import { li2yuan } from "@/lib/money";
import { formatDateTime } from "@/lib/datetime";

/** 需通过调用方能直接变更的最小 mutation 形状。 */
interface PayMutations {
  simulate: {
    isPending: boolean;
    mutate: (result: "SUCCESS" | "FAILURE") => void;
  };
  cancel: { isPending: boolean; mutate: () => void };
}

/** 待支付:模拟收款码与「成功/失败/取消」三个操作。 */
function CreatedBlock({ busy, simulate, cancel }: { busy: boolean } & PayMutations) {
  return (
    <>
      <div
        className="mx-auto flex size-40 items-center justify-center rounded-lg border border-dashed border-border bg-muted/40"
        aria-label="模拟收款码"
      >
        <QrCode className="size-24 text-muted-foreground" aria-hidden />
      </div>
      <p className="text-xs text-muted-foreground">
        这是模拟收款码,仅作演示。点击下方按钮可模拟支付成功或失败。
      </p>
      <div className="flex justify-center gap-2">
        <Button onClick={() => simulate.mutate("SUCCESS")} disabled={busy}>
          {simulate.isPending && <Loader2 className="animate-spin" aria-hidden />}
          模拟支付成功
        </Button>
        <Button variant="outline" onClick={() => simulate.mutate("FAILURE")} disabled={busy}>
          模拟支付失败
        </Button>
        <Button variant="ghost" onClick={() => cancel.mutate()} disabled={busy}>
          取消支付
        </Button>
      </div>
    </>
  );
}

interface PayStatusBlockProps {
  pay: PaymentItem;
  busy: boolean;
  simulate: PayMutations["simulate"];
  cancel: PayMutations["cancel"];
}

/** 账单周期与金额。 */
export function PayAmountHeader({
  pay,
  invoice,
}: {
  pay: PaymentItem;
  invoice: InvoiceDetail | undefined;
}) {
  return (
    <div className="space-y-1">
      <p className="text-sm text-muted-foreground">账单周期 {invoice?.period ?? "…"}</p>
      <p className="text-3xl font-semibold tabular-nums">¥{li2yuan(pay.amount)}</p>
    </div>
  );
}

/** 当前状态对应的操作或结果区。 */
export function PayStatusBlock({ pay, busy, simulate, cancel }: PayStatusBlockProps) {
  return (
    <>
      {pay.status === "CREATED" && <CreatedBlock busy={busy} simulate={simulate} cancel={cancel} />}

      {pay.status === "PAID" && (
        <div className="space-y-2" role="status">
          <CheckCircle2 className="mx-auto size-10 text-primary" aria-hidden />
          <p className="font-medium">支付成功</p>
          <p className="text-xs text-muted-foreground">
            交易号 {pay.trade_no} · {formatDateTime(pay.paid_at)}
          </p>
        </div>
      )}
      {pay.status === "CANCELLED" && (
        <div className="space-y-2" role="status">
          <XCircle className="mx-auto size-10 text-muted-foreground" aria-hidden />
          <p className="font-medium">已取消</p>
          <p className="text-xs text-muted-foreground">可回门户重新发起支付。</p>
        </div>
      )}
      {pay.status === "EXPIRED" && (
        <div className="space-y-2" role="status">
          <AlertTriangle className="mx-auto size-10 text-muted-foreground" aria-hidden />
          <p className="font-medium">支付单已过期</p>
          <p className="text-xs text-muted-foreground">超过 30 分钟未支付,可回门户重新发起。</p>
        </div>
      )}
    </>
  );
}
