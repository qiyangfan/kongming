/* 展示开发者用量和账单；API 密钥管理入口复用 Bot 管理页。 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  createPayment,
  getInvoiceDetail,
  getPortalUsage,
  listPortalInvoices,
  listPortalKeys,
  type InvoiceDetail,
} from "@/lib/billing-api";

import { InvoiceDetailBody, InvoicesSection } from "./portal-invoices";
import { UsageSection } from "./portal-usage";

/** 支付发起失败时把服务端错误码映射为用户可读文案。 */
function payErrorMessage(e: unknown): string {
  const raw = e instanceof Error ? e.message : "";
  if (raw.startsWith("INVOICE_PAID")) return "该账单已支付,无需重复付款";
  if (raw.startsWith("INVOICE_VOID")) return "该账单已作废,不可支付";
  return raw || "发起支付失败";
}

export default function PortalPage() {
  const navigate = useNavigate();
  const usage = useQuery({ queryKey: ["portal", "usage"], queryFn: getPortalUsage });
  const invoices = useQuery({
    queryKey: ["portal", "invoices"],
    queryFn: listPortalInvoices,
  });
  const keys = useQuery({ queryKey: ["portal", "keys"], queryFn: listPortalKeys });

  // 明细对话框:先清空旧明细再请求,防闪现上一条内容
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);

  const [payingId, setPayingId] = useState<string | null>(null);
  const [payError, setPayError] = useState("");

  async function openDetail(id: string): Promise<void> {
    setDetailId(id);
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    try {
      setDetail(await getInvoiceDetail(id));
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : "明细加载失败");
    } finally {
      setDetailLoading(false);
    }
  }

  // 创建支付单后进入收银台;同一账单重付时由服务端作废旧单。
  async function goPay(invoiceId: string): Promise<void> {
    setPayingId(invoiceId);
    setPayError("");
    try {
      const payment = await createPayment(invoiceId);
      void navigate(`/portal/pay/${payment.id}`);
    } catch (e) {
      setPayError(payErrorMessage(e));
    } finally {
      setPayingId(null);
    }
  }

  return (
    <div className="space-y-5 px-6 py-5">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">用量与密钥</h1>
        {usage.data?.plan && <Badge variant="secondary">套餐:{usage.data.plan.name}</Badge>}
      </div>

      <UsageSection usage={usage} keys={keys} />

      <InvoicesSection
        invoices={invoices.data}
        isPending={invoices.isPending}
        isError={invoices.isError}
        payingId={payingId}
        payError={payError}
        onDetail={(id) => void openDetail(id)}
        onPay={(id) => void goPay(id)}
      />

      <Dialog open={detailId !== null} onOpenChange={(o) => !o && setDetailId(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>账单明细 {detail?.period}</DialogTitle>
          </DialogHeader>
          {detailLoading && (
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
          )}
          {detailError && (
            <p role="alert" className="text-sm text-destructive">
              {detailError}
            </p>
          )}
          {detail && <InvoiceDetailBody detail={detail} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
