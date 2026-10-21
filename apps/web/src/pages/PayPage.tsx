/* 模拟支付页：由服务端模拟成功或失败，前端不持有密钥或直接修改支付单。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { AlertTriangle, CheckCircle2, Loader2, QrCode, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { ErrorAlert } from "@/components/error-alert";
import { li2yuan } from "@/lib/money";
import { cancelPayment, getInvoiceDetail, getPayment, mockSimulate } from "@/lib/billing-api";
import { formatDateTime } from "@/lib/datetime";

const TERMINAL = new Set(["PAID", "CANCELLED", "EXPIRED"]);

export default function PayPage() {
  const { paymentId = "" } = useParams();
  const qc = useQueryClient();
  const [error, setError] = useState("");

  const payment = useQuery({
    queryKey: ["portal", "payment", paymentId],
    queryFn: () => getPayment(paymentId),
    enabled: paymentId !== "",
    // 非终态继续轮询；支付单进入终态或请求返回错误后停止。
    refetchInterval: (q) => {
      if (q.state.status === "error") return false;
      return q.state.data && TERMINAL.has(q.state.data.status) ? false : 2000;
    },
  });
  const invoice = useQuery({
    queryKey: ["portal", "invoice", payment.data?.invoice_id ?? ""],
    queryFn: () => getInvoiceDetail(payment.data!.invoice_id),
    enabled: payment.data?.invoice_id != null,
  });

  const refresh = () => void qc.invalidateQueries({ queryKey: ["portal", "payment", paymentId] });

  const simulate = useMutation({
    mutationFn: (result: "SUCCESS" | "FAILURE") => mockSimulate(paymentId, result),
    onSuccess: () => {
      setError("");
      refresh();
    },
    onError: (e) => setError(e instanceof Error ? e.message : "模拟支付失败"),
  });
  const cancel = useMutation({
    mutationFn: () => cancelPayment(paymentId),
    onSuccess: () => {
      setError("");
      refresh();
    },
    onError: (e) => setError(e instanceof Error ? e.message : "取消失败"),
  });

  const pay = payment.data;
  const busy = simulate.isPending || cancel.isPending;

  return (
    <div className="mx-auto max-w-md space-y-5 px-6 py-10">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">收银台</h1>
        <Badge variant="outline">模拟环境 · 无真实资金</Badge>
      </div>

      {payment.isLoading ? (
        <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
      ) : payment.isError || !pay ? (
        <ErrorAlert text="支付单不存在或不可见。" />
      ) : (
        <section className="space-y-4 rounded-lg border border-border p-6 text-center">
          <div className="space-y-1">
            <p className="text-sm text-muted-foreground">账单周期 {invoice.data?.period ?? "…"}</p>
            <p className="text-3xl font-semibold tabular-nums">¥{li2yuan(pay.amount)}</p>
          </div>

          {pay.status === "CREATED" && (
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
                <Button
                  variant="outline"
                  onClick={() => simulate.mutate("FAILURE")}
                  disabled={busy}
                >
                  模拟支付失败
                </Button>
                <Button variant="ghost" onClick={() => cancel.mutate()} disabled={busy}>
                  取消支付
                </Button>
              </div>
            </>
          )}

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

          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </section>
      )}

      <p className="text-center text-sm">
        <Link to="/portal" className={buttonVariants({ variant: "link" })}>
          返回门户
        </Link>
      </p>
    </div>
  );
}
