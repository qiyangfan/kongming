/* 开发者门户页(034 developer-portal-billing spec「开发者门户」):
   当期三维用量看板(进度条+预警标记)+ 账单列表与明细对话框。
   api-key 列表复用 Bots 页既有入口(导航指引),不重复建设。 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { QueryErrorAlert } from "@/components/query-error";
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

/** 明细 usage_json 形状(服务端固定结构;生成类型为 generic object,
 *  本地收窄——与服务端 usage_json 组装处逐字段对应) */
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

/** 行费用(整数厘):与服务端 estimate_cost 同式(整数运算,向下取整)。
 *  服务端金额按合计一次计算,各行求和与总额至多差尾数取整(厘级),总额以 amount 为准。 */
function rowCostLi(
  row: { prompt: number; completion: number; calls: number },
  u: UsageDetail,
): number {
  return (
    Math.floor(((row.prompt + row.completion) * (u.price_per_mtok ?? 0)) / 1000) +
    row.calls * (u.price_per_call ?? 0)
  );
}

/** 计费公式文案:单价代入算式,替代裸单价展示(用户可自行验算)。
 *  每个计费项独占一行——窄容器里自然折行断点不可控(可能断在单价/等号中间),主动断行钉在语义边界 */
function formulaLines(u: UsageDetail): string[] {
  const tokens = u.total_tokens ?? 0;
  const calls = u.total_calls ?? 0;
  const tokenFee = Math.floor((tokens * (u.price_per_mtok ?? 0)) / 1000);
  return [
    "按用量计费:",
    `Token ${tokens.toLocaleString()} × ¥${li2yuan(u.price_per_mtok ?? 0)}/千 = ¥${li2yuan(tokenFee)}`,
    `调用 ${calls.toLocaleString()} 次 × ¥${li2yuan(u.price_per_call ?? 0)} = ¥${li2yuan(calls * (u.price_per_call ?? 0))}`,
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
  const fmt = format ?? ((n: number) => n.toLocaleString());
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
  const limitOf = (dim: string) =>
    policies.find((p) => p.dimension === dim)?.limit_value ?? 0;
  const warnOf = (dim: string) =>
    policies.find((p) => p.dimension === dim)?.warn_pct ?? 80;

  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);

  async function openDetail(id: string): Promise<void> {
    // R1-A8:打开即清空(防闪现上一条)+加载态+错误收敛
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

  // 035:去支付——创建(或重发)支付单后进收银台;同账单重付旧单服务端作废旧单
  async function goPay(invoiceId: string): Promise<void> {
    setPayingId(invoiceId);
    setPayError("");
    try {
      const payment = await createPayment(invoiceId);
      navigate(`/portal/pay/${payment.id}`);
    } catch (e) {
      // R1-T4:409 错误码映射友好文案(原始码 INVOICE_PAID 不直抛用户)
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
        {usage.data?.plan && (
          <Badge variant="secondary" className="font-normal">
            套餐:{usage.data.plan.name}
          </Badge>
        )}
      </div>

      <section className="space-y-3 rounded-lg border border-border p-4">
        <h2 className="text-sm font-medium">
          当期用量({usage.data?.month ?? "…"})
        </h2>
        {usage.isError ? (
          <QueryErrorAlert />
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
              <p className="text-xs font-medium text-muted-foreground">我的 API 密钥(仅显示生效中)</p>
              {keys.isError ? (
                <QueryErrorAlert />
              ) : (keys.data ?? []).length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  还没有密钥,到「管理 → 助手」页,在助手列表中点「创建密钥」。
                </p>
              ) : (
                <ul className="space-y-0.5 text-xs text-muted-foreground">
                  {(keys.data ?? []).map((k) => (
                    <li key={k.id} className="font-mono">
                      {k.bot_key}:{k.name}
                      {k.last_used_at
                        ? `(最近用 ${new Date(k.last_used_at).toLocaleDateString()})`
                        : "(未使用)"}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium">账单</h2>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>周期</TableHead>
                <TableHead>金额(元)</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invoices.isError ? (
                <TableRow>
                  <TableCell colSpan={4}>
                    <QueryErrorAlert />
                  </TableCell>
                </TableRow>
              ) : (invoices.data ?? []).length === 0 ? (
                <TableRow>
                  <TableCell colSpan={4} className="py-8 text-center text-muted-foreground">
                    暂无账单(每月 1 日出账)。
                  </TableCell>
                </TableRow>
              ) : (
                (invoices.data ?? []).map((inv) => (
                  <TableRow key={inv.id}>
                    <TableCell className="font-mono text-xs">{inv.period}</TableCell>
                    <TableCell>¥{li2yuan(inv.amount)}</TableCell>
                    <TableCell>
                      <Badge
                        variant={inv.status === "PENDING" ? "secondary" : "outline"}
                        className="font-normal"
                      >
                        {INVOICE_STATUS_TEXT[inv.status] ?? inv.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
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
                      <Button variant="ghost" size="xs" onClick={() => void openDetail(inv.id)}>
                        明细
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
        {payError && (
          <p role="alert" className="text-sm text-destructive">
            {payError}
          </p>
        )}
      </section>

      {/* 明细对话框 */}
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
                金额:<strong>¥{li2yuan(detail.amount)}</strong> · 状态:{INVOICE_STATUS_TEXT[detail.status] ?? detail.status}
              </p>
              {(usageOf(detail).by_model ?? []).length > 0 && (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>模型</TableHead>
                      <TableHead>输入 Token</TableHead>
                      <TableHead>输出 Token</TableHead>
                      <TableHead>调用次数</TableHead>
                      <TableHead className="text-right">费用</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(usageOf(detail).by_model ?? []).map((m) => (
                      <TableRow key={m.model}>
                        <TableCell className="max-w-40 truncate font-mono text-xs" title={m.model}>{m.model}</TableCell>
                        <TableCell>{m.prompt.toLocaleString()}</TableCell>
                        <TableCell>{m.completion.toLocaleString()}</TableCell>
                        <TableCell>{m.calls}</TableCell>
                        <TableCell className="text-right">
                          ¥{li2yuan(rowCostLi(m, usageOf(detail)))}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
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
