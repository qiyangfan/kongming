/* 反馈分析页(feedback-analytics spec 014「前端页面」):统计卡 + 趋势折线 +
   筛选区 + 明细分页表(处置状态列) + 详情弹窗(对照/涉及表/处置区按权限显隐)。
   页面只读(处置在弹窗内);图表色板从设计 token 派生(铁律 6);
   无 system:feedback:view 者接口层 403(nav/PermRoute 同源守卫)。 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { Link } from "react-router";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuth } from "@/lib/auth-store";
import { datasourceApi } from "@/lib/datasource-api";
import {
  feedbackOverview,
  feedbackPage,
  handleFeedback,
  involvedTables,
  type FeedbackPageItemOut,
} from "@/lib/feedback-api";
import { hasPerm } from "@/lib/nav";
import { systemApi } from "@/lib/system-api";

const PAGE_SIZE = 20;

const STATUS_META: Record<string, { label: string; variant: "default" | "secondary" | "outline" }> = {
  PENDING: { label: "待处置", variant: "outline" },
  ADOPTED: { label: "已采纳", variant: "default" },
  REJECTED: { label: "已驳回", variant: "secondary" },
  DISMISSED: { label: "已忽略", variant: "secondary" },
};

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export default function FeedbackPage() {
  const { user } = useAuth();
  const canHandle = hasPerm(user, "system:feedback:handle");
  const [days, setDays] = useState(30);
  const [rating, setRating] = useState("");
  const [status, setStatus] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [dsKey, setDsKey] = useState("");
  const [onlyCorrected, setOnlyCorrected] = useState(false);
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<FeedbackPageItemOut | null>(null);

  // 租户清单挂 system:user:manage——仅 view 权限者 403,降级隐藏该筛选项
  const tenants = useQuery({
    queryKey: ["system", "tenants"],
    queryFn: systemApi.listTenants,
    retry: false,
  });
  const dsOptions = useQuery({
    queryKey: ["feedback", "ds-options"],
    queryFn: datasourceApi.options,
  });

  const overview = useQuery({
    queryKey: ["feedback", "overview", days, tenantId, dsKey],
    queryFn: () =>
      feedbackOverview({
        days,
        ...(tenantId ? { tenant_id: tenantId } : {}),
        ...(dsKey ? { datasource_key: dsKey } : {}),
      }),
  });

  const list = useQuery({
    queryKey: ["feedback", "page", page, rating, status, tenantId, dsKey, onlyCorrected],
    queryFn: () =>
      feedbackPage({
        page,
        size: PAGE_SIZE,
        ...(rating ? { rating: rating as "UP" | "DOWN" } : {}),
        ...(status
          ? { feedback_status: status as "PENDING" | "ADOPTED" | "REJECTED" | "DISMISSED" }
          : {}),
        ...(tenantId ? { tenant_id: tenantId } : {}),
        ...(dsKey ? { datasource_key: dsKey } : {}),
        ...(onlyCorrected ? { only_corrected: true } : {}),
      }),
  });

  // 删除/处置致末页越界自愈(007/009 同款范式)
  useEffect(() => {
    if (list.data && list.data.items.length === 0 && list.data.total > 0 && page > 1) {
      setPage((p) => Math.max(1, p - 1));
    }
  }, [list.data, page]);

  const trendOption = useMemo(() => {
    const trend = overview.data?.trend ?? [];
    const axisColor = cssVar("--muted-foreground");
    const lineColor = cssVar("--border");
    const axisCommon = {
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: lineColor } },
    };
    return {
      tooltip: { trigger: "axis" as const },
      legend: { bottom: 0, left: "center", textStyle: { color: axisColor } },
      grid: { left: 12, right: 16, top: 24, bottom: 28, containLabel: true },
      xAxis: {
        type: "category" as const,
        data: trend.map((t) => t.day.slice(5)), // MM-DD 省宽
        ...axisCommon,
      },
      yAxis: { type: "value" as const, minInterval: 1, ...axisCommon },
      series: [
        {
          name: "赞",
          type: "line" as const,
          data: trend.map((t) => t.up),
          smooth: true,
          itemStyle: { color: cssVar("--ring") },
          lineStyle: { color: cssVar("--ring"), width: 2 },
        },
        {
          name: "踩",
          type: "line" as const,
          data: trend.map((t) => t.down),
          smooth: true,
          itemStyle: { color: cssVar("--primary") },
          lineStyle: { color: cssVar("--primary"), width: 2 },
        },
      ],
    };
  }, [overview.data]);

  const ov = overview.data;
  const totalPages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">反馈分析</h2>
        <p className="text-sm text-muted-foreground">
          用户反馈的统计与明细——处置反馈、沉淀纠错,让军师越答越准。
        </p>
      </div>

      {/* 统计卡(feedback-analytics spec) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatCard label="反馈总数" value={ov?.total} loading={overview.isLoading} />
        <StatCard label="有用(赞)" value={ov?.up_count} loading={overview.isLoading} />
        <StatCard label="不准确(踩)" value={ov?.down_count} loading={overview.isLoading} />
        <StatCard
          label="差评率"
          value={ov ? `${(ov.down_rate * 100).toFixed(1)}%` : undefined}
          loading={overview.isLoading}
        />
        <StatCard label="带纠错" value={ov?.corrected_count} loading={overview.isLoading} />
      </div>

      {/* 趋势折线(token 色板) */}
      <div className="rounded-lg border border-border bg-card px-2 py-1">
        <ReactECharts
          option={trendOption}
          notMerge
          lazyUpdate
          style={{ height: 220, width: "100%" }}
          opts={{ renderer: "canvas" }}
        />
      </div>

      {/* 筛选区 */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">评分</Label>
          <Select value={rating || "ALL"} onValueChange={(v) => { setRating(v === "ALL" ? "" : v); setPage(1); }}>
            <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">全部</SelectItem>
              <SelectItem value="UP">有用</SelectItem>
              <SelectItem value="DOWN">不准确</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">处置状态</Label>
          <Select value={status || "ALL"} onValueChange={(v) => { setStatus(v === "ALL" ? "" : v); setPage(1); }}>
            <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">全部</SelectItem>
              <SelectItem value="PENDING">待处置</SelectItem>
              <SelectItem value="ADOPTED">已采纳</SelectItem>
              <SelectItem value="REJECTED">已驳回</SelectItem>
              <SelectItem value="DISMISSED">已忽略</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {tenants.isSuccess && (
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">租户</Label>
            <Select value={tenantId || "ALL"} onValueChange={(v) => { setTenantId(v === "ALL" ? "" : v); setPage(1); }}>
              <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">全部</SelectItem>
                {tenants.data.map((t) => (
                  <SelectItem key={t.id} value={t.id}>{t.tenant_name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">数据源</Label>
          <Select value={dsKey || "ALL"} onValueChange={(v) => { setDsKey(v === "ALL" ? "" : v); setPage(1); }}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">全部</SelectItem>
              {(dsOptions.data ?? []).map((o) => (
                <SelectItem key={o.datasource_key} value={o.datasource_key}>{o.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">时间范围</Label>
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="7">近 7 天</SelectItem>
              <SelectItem value="30">近 30 天</SelectItem>
              <SelectItem value="90">近 90 天</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <label className="flex items-center gap-2 pb-1.5 text-sm">
          <Checkbox
            checked={onlyCorrected}
            onCheckedChange={(c) => { setOnlyCorrected(c === true); setPage(1); }}
          />
          仅看带纠错
        </label>
      </div>

      {/* 明细分页表 */}
      <div className="overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>时间</TableHead>
              <TableHead>用户</TableHead>
              <TableHead>租户</TableHead>
              <TableHead>数据源</TableHead>
              <TableHead>评分</TableHead>
              <TableHead>问句</TableHead>
              <TableHead>纠错</TableHead>
              <TableHead>处置状态</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.isLoading ? (
              <TableRow>
                <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">
                  <Loader2 className="inline size-4 animate-spin" aria-hidden /> 加载中…
                </TableCell>
              </TableRow>
            ) : (list.data?.items.length ?? 0) === 0 ? (
              <TableRow>
                <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">
                  暂无反馈——问答卡片右下角即可赞踩。
                </TableCell>
              </TableRow>
            ) : (
              list.data?.items.map((item) => (
                <TableRow key={item.message_id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {item.feedback_time ? item.feedback_time.slice(0, 16).replace("T", " ") : "—"}
                  </TableCell>
                  <TableCell>{item.user_nickname ?? item.user_id.slice(0, 8)}</TableCell>
                  <TableCell>{item.tenant_key}</TableCell>
                  <TableCell>{item.datasource_key}</TableCell>
                  <TableCell>
                    {item.rating === "UP" ? (
                      <Badge variant="secondary">有用</Badge>
                    ) : item.rating === "DOWN" ? (
                      <Badge>不准确</Badge>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="max-w-56 truncate" title={item.question ?? ""}>
                    {item.question ?? "—"}
                  </TableCell>
                  <TableCell>{item.corrected_sql ? <Badge variant="outline">带纠错</Badge> : "—"}</TableCell>
                  <TableCell>
                    <Badge variant={STATUS_META[item.feedback_status]?.variant ?? "outline"}>
                      {STATUS_META[item.feedback_status]?.label ?? item.feedback_status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="xs" onClick={() => setDetail(item)}>
                      详情
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>共 {list.data?.total ?? 0} 条</span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="xs"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </Button>
          <span>
            {page} / {totalPages}
          </span>
          <Button
            variant="outline"
            size="xs"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      </div>

      {detail && (
        <FeedbackDetailDialog
          item={detail}
          canHandle={canHandle}
          onClose={() => setDetail(null)}
          onHandled={() => {
            setDetail(null);
            void list.refetch();
            void overview.refetch();
          }}
        />
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  loading,
}: {
  label: string;
  value: number | string | undefined;
  loading: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">
        {loading ? "…" : (value ?? 0)}
      </p>
    </div>
  );
}

/* 详情弹窗(feedback-refine spec):对照区 + 涉及表区(「编辑知识」跳转)+
   处置区(无 handle 权限隐藏) */
function FeedbackDetailDialog({
  item,
  canHandle,
  onClose,
  onHandled,
}: {
  item: FeedbackPageItemOut;
  canHandle: boolean;
  onClose: () => void;
  onHandled: () => void;
}) {
  const [status, setStatus] = useState(item.feedback_status);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const tables = useQuery({
    queryKey: ["feedback", "involved-tables", item.message_id],
    queryFn: () => involvedTables(item.message_id),
  });

  const save = useMutation({
    mutationFn: () =>
      handleFeedback(item.message_id, {
        status,
        ...(note ? { note } : {}),
      }),
    onSuccess: () => onHandled(),
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !save.isPending) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>反馈详情</DialogTitle>
          <DialogDescription>
            {item.user_nickname ?? item.user_id.slice(0, 8)} · {item.tenant_key} ·{" "}
            {item.datasource_key}
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[55vh] space-y-4 overflow-y-auto pr-1">
          <section>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">问句</h3>
            <p className="text-sm">{item.question ?? "—"}</p>
          </section>
          <section>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">原始 SQL</h3>
            <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
              {item.sql_text ?? "—"}
            </pre>
          </section>
          <section>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">纠错 SQL</h3>
            <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
              {item.corrected_sql ?? "—"}
            </pre>
          </section>
          <section>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">留言</h3>
            <p className="text-sm">{item.feedback_comment ?? "—"}</p>
          </section>

          <section>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">涉及表</h3>
            {tables.isLoading ? (
              <p className="text-xs text-muted-foreground">
                <Loader2 className="inline size-3 animate-spin" aria-hidden /> 定位中…
              </p>
            ) : tables.data?.parse_error ? (
              <p className="text-xs text-muted-foreground">
                SQL 解析失败,请手动前往知识库编辑。
              </p>
            ) : (tables.data?.tables.length ?? 0) === 0 ? (
              <p className="text-xs text-muted-foreground">未定位到表。</p>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                {tables.data?.tables.map((t) => (
                  <span key={t.table_name} className="inline-flex items-center gap-1.5">
                    <Badge variant="outline">{t.table_name}</Badge>
                    {t.has_knowledge && (
                      <Button variant="link" size="xs" asChild>
                        <Link to={`/admin/rag?table=${encodeURIComponent(t.table_name)}`}>
                          编辑知识
                        </Link>
                      </Button>
                    )}
                  </span>
                ))}
              </div>
            )}
          </section>

          {canHandle && (
            <section className="space-y-3 rounded-lg border border-border p-3">
              <h3 className="text-xs font-medium text-muted-foreground">处置</h3>
              <div className="flex items-end gap-3">
                <div className="space-y-1">
                  <Label className="text-xs">状态</Label>
                  <Select value={status} onValueChange={setStatus}>
                    <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="PENDING">待处置</SelectItem>
                      <SelectItem value="ADOPTED">已采纳</SelectItem>
                      <SelectItem value="REJECTED">已驳回</SelectItem>
                      <SelectItem value="DISMISSED">已忽略</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex-1 space-y-1">
                  <Label className="text-xs">处理备注</Label>
                  <Input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="处置说明(可留空)"
                    maxLength={500}
                  />
                </div>
              </div>
              {error && (
                <p role="alert" className="text-xs text-destructive">
                  {error}
                </p>
              )}
            </section>
          )}
        </div>

        {canHandle && (
          <DialogFooter>
            <Button onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
              保存处置
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
