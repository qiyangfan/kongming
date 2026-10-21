/* 展示开发者用量和账单；API 密钥管理入口复用 Bot 管理页。 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import {
  createPayment,
  getInvoiceDetail,
  getPortalUsage,
  listPortalInvoices,
  listPortalKeys,
  type InvoiceDetail,
} from "@/lib/billing-api";
import { DIM_TEXT, INVOICE_STATUS_TEXT } from "@/lib/billing-labels";
import { li2yuan } from "@/lib/money";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { formatDate, formatNumber } from "@/lib/datetime";

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

function UsageBar({
  label,
  used,
  limit,
  warnPct,
  format,
}: {
  label: string;
  used: number;
  limit: number;
  warnPct: number;
  /** 值格式化(如金额维度传 li2yuan 转元);缺省千分位整数 */
  format?: (n: number) => string;
}) {
  const fmt = format ?? formatNumber;
  if (limit <= 0) {
    return (
      <div className="space-y-1">
        <p className="text-sm">
          {label}: {fmt(used)}(不限)
        </p>
      </div>
    );
  }
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const warn = used * 100 >= limit * warnPct;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span>{label}</span>
        <span className="text-muted-foreground">
          {fmt(used)} / {fmt(limit)}({pct}%)
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full ${warn ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {warn && (
        <p role="alert" className="text-xs text-destructive">
          已达预警线({warnPct}%),请注意用量。
        </p>
      )}
    </div>
  );
}

export default function PortalPage() {
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const navigate = useNavigate();
  const [payingId, setPayingId] = useState<string | null>(null);
  const [payError, setPayError] = useState("");

  const usage = useQuery({ queryKey: ["portal", "usage"], queryFn: getPortalUsage });
  const invoices = useQuery({
    queryKey: ["portal", "invoices"],
    queryFn: listPortalInvoices,
  });
  const keys = useQuery({ queryKey: ["portal", "keys"], queryFn: listPortalKeys });

  const policies = usage.data?.policies ?? [];
  const limitOf = (dim: string) => policies.find((p) => p.dimension === dim)?.limit_value ?? 0;
  const warnOf = (dim: string) => policies.find((p) => p.dimension === dim)?.warn_pct ?? 80;

  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);

  async function openDetail(id: string): Promise<void> {
    // 先清空旧明细再请求,防闪现上一条内容。
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
      navigate(`/portal/pay/${payment.id}`);
    } catch (e) {
      // 原始错误码(INVOICE_PAID 等)映射为用户可读文案。
      const raw = e instanceof Error ? e.message : "";
      const msg = raw.startsWith("INVOICE_PAID")
        ? "该账单已支付,无需重复付款"
        : raw.startsWith("INVOICE_VOID")
          ? "该账单已作废,不可支付"
          : raw || "发起支付失败";
      setPayError(msg);
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

      <section className="space-y-3 rounded-lg border border-border p-4">
        <h2 className="text-sm font-medium">当期用量({usage.data?.month ?? "…"})</h2>
        {usage.isError ? (
          <ErrorAlert />
        ) : usage.isLoading ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
        ) : (
          <>
            <UsageBar
              label={DIM_TEXT.TOKENS}
              used={usage.data?.tokens ?? 0}
              limit={limitOf("TOKENS")}
              warnPct={warnOf("TOKENS")}
            />
            <UsageBar
              label={DIM_TEXT.CALLS}
              used={usage.data?.calls ?? 0}
              limit={limitOf("CALLS")}
              warnPct={warnOf("CALLS")}
            />
            <UsageBar
              label={DIM_TEXT.COST}
              used={usage.data?.cost ?? 0}
              limit={limitOf("COST")}
              warnPct={warnOf("COST")}
              format={li2yuan}
            />
            <div className="space-y-1 pt-2">
              <p className="text-xs font-medium text-muted-foreground">
                我的 API 密钥(仅显示生效中)
              </p>
              {keys.isError ? (
                <ErrorAlert />
              ) : (
                <DataView
                  rows={keys.data ?? []}
                  rowKey={(k) => k.id}
                  isLoading={keys.isPending}
                  isEmpty={!keys.isPending && keys.data?.length === 0}
                  emptyText="还没有密钥,到「管理 → 助手」页,在助手列表中点「创建密钥」。"
                  columns={[
                    {
                      key: "bot_key",
                      header: "密钥",
                      cardPriority: 1,
                      render: (k) => (
                        <span className="font-mono text-xs text-muted-foreground">{k.bot_key}</span>
                      ),
                    },
                    {
                      key: "name",
                      header: "名称",
                      cardPriority: 2,
                      render: (k) => (
                        <span className="text-xs text-muted-foreground">{k.name}</span>
                      ),
                    },
                    {
                      key: "last_used_at",
                      header: "最近使用",
                      cardPriority: 3,
                      cellClassName: "text-xs text-muted-foreground",
                      render: (k) =>
                        k.last_used_at ? `最近用 ${formatDate(k.last_used_at)}` : "未使用",
                    },
                  ]}
                />
              )}
            </div>
          </>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">账单</h2>
        {invoices.isError && <ErrorAlert />}
        {/* 外框仅在宽屏保留(md- 卡片形态裸露,与 DataView 样板口径一致) */}
        <div className="md:rounded-lg md:border">
          <DataView
            rows={invoices.data ?? []}
            rowKey={(inv) => inv.id}
            isLoading={invoices.isPending}
            isEmpty={!invoices.isPending && invoices.data?.length === 0}
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
                    {/* 去支付仅 PENDING 可见;DataViewAction 无按行条件,故挂状态列而非 actions */}
                    {inv.status === "PENDING" && (
                      <Button
                        variant="outline"
                        size="xs"
                        disabled={payingId === inv.id}
                        onClick={() => void goPay(inv.id)}
                      >
                        {payingId === inv.id && <Loader2 className="animate-spin" aria-hidden />}
                        去支付
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
            actions={[{ label: "明细", onSelect: (inv) => void openDetail(inv.id) }]}
          />
        </div>
        {payError && (
          <p role="alert" className="text-sm text-destructive">
            {payError}
          </p>
        )}
      </section>

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
          {detail && (
            <div className="space-y-3 text-sm">
              <p>
                金额:<strong>¥{li2yuan(detail.amount)}</strong> · 状态:
                {INVOICE_STATUS_TEXT[detail.status] ?? detail.status}
              </p>
              {(usageOf(detail).by_model ?? []).length > 0 && (
                <DataView
                  rows={usageOf(detail).by_model ?? []}
                  rowKey={(m) => m.model}
                  columns={[
                    {
                      key: "model",
                      header: "模型",
                      cardPriority: 1,
                      render: (m) => (
                        <span className="block max-w-40 truncate font-mono text-xs" title={m.model}>
                          {m.model}
                        </span>
                      ),
                    },
                    { key: "prompt", header: "输入 Token", render: (m) => formatNumber(m.prompt) },
                    {
                      key: "completion",
                      header: "输出 Token",
                      render: (m) => formatNumber(m.completion),
                    },
                    { key: "calls", header: "调用次数", render: (m) => formatNumber(m.calls) },
                    {
                      key: "cost",
                      header: "费用",
                      headClassName: "text-right",
                      cellClassName: "text-right",
                      render: (m) => `¥${li2yuan(rowCostLi(m, usageOf(detail)))}`,
                    },
                  ]}
                />
              )}
              <div className="space-y-1 text-xs text-muted-foreground">
                {formulaLines(usageOf(detail)).map((line) => (
                  <p key={line}>{line}</p>
                ))}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
