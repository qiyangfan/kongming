/* 模拟支付页：由服务端模拟成功或失败，前端不持有密钥或直接修改支付单。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { ErrorAlert } from "@/components/error-alert";
import { cancelPayment, getInvoiceDetail, getPayment, mockSimulate } from "@/lib/billing-api";

import { PayAmountHeader, PayStatusBlock } from "./pay-status-block";

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
    // React Query 约定该回调返回 number | false,库类型决定无法收敛成单一类型
    // eslint-disable-next-line sonarjs/function-return-type
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

      {(() => {
        if (payment.isLoading) {
          return <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />;
        }
        if (payment.isError || !pay) {
          return <ErrorAlert text="支付单不存在或不可见。" />;
        }
        return (
          <section className="space-y-4 rounded-lg border border-border p-6 text-center">
            <PayAmountHeader pay={pay} invoice={invoice.data} />
            <PayStatusBlock pay={pay} busy={busy} simulate={simulate} cancel={cancel} />

            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </section>
        );
      })()}

      <p className="text-center text-sm">
        <Link to="/portal" className={buttonVariants({ variant: "link" })}>
          返回门户
        </Link>
      </p>
    </div>
  );
}
