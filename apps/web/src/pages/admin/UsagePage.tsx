/* 用量观测页(038 agent-observability):LLM 调用/失败/延迟的可观测面板——
   汇总卡 + 按天趋势(双轴:调用/失败 vs tokens)+ 模型×任务对比表 +
   明细分页(四类筛选)+ trace 时间线对话框(LLM/工具/SQL 三路事件流)。
   图表色板从设计 token 派生(铁律 6);类型全生成(ADR-005);
   无 system:llm-usage:view 者接口层 403(nav/PermRoute 同源守卫)。 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { Activity, Database, Sparkles, Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import {
  usagePage,
  usageSummary,
  usageTimeline,
  usageTrend,
  type TimelineEvent,
} from "@/lib/usage-api";

const PAGE_SIZE = 20;

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** R1-B6:输入型筛选防抖(300ms)——每键一查的请求放大收敛 */
function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(1)}s`;
  return `${v}ms`;
}

function fmtTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 10_000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

const KIND_ICON = { LLM: Sparkles, TOOL: Wrench, SQL: Database } as const;

const TOOL_STATUS_LABEL: Record<string, string> = {
  EXECUTED: "已执行",
  REJECTED: "已拒绝",
  ERROR: "错误",
  RATE_LIMITED: "限流",
  PENDING_CONFIRM: "待确认",
  CONFIRM_REJECTED: "确认拒绝",
  CONFIRM_EXPIRED: "确认过期",
};

// R1-B8:SQL verdict 与 LLM status 的中文映射(与 TOOL 同面)
const VERDICT_LABEL: Record<string, string> = {
  PASS: "通过",
  BLOCKED: "拦截",
  ERROR: "错误",
};
const LLM_STATUS_LABEL: Record<string, string> = {
  SUCCESS: "成功",
  ERROR: "失败",
};

function kindStatusLabel(kind: string, status: string): string {
  const table =
    kind === "TOOL" ? TOOL_STATUS_LABEL : kind === "SQL" ? VERDICT_LABEL : LLM_STATUS_LABEL;
  return table[status] ?? status;
}

export default function UsagePage() {
  const [days, setDays] = useState(30);
  const [model, setModel] = useState("");
  const [task, setTask] = useState("");
  const [status, setStatus] = useState("");
  const [traceId, setTraceId] = useState("");
  const [page, setPage] = useState(1);
  const [timelineTrace, setTimelineTrace] = useState<string | null>(null);

  // R1-B6:防抖值驱动查询(输入即时回显,查询 300ms 收敛)
  const modelQ = useDebounced(model);
  const taskQ = useDebounced(task);
  const traceIdQ = useDebounced(traceId);

  const summary = useQuery({
    queryKey: ["usage", "summary"],
    queryFn: () => usageSummary(),
  });
  const trend = useQuery({
    queryKey: ["usage", "trend", days],
    queryFn: () => usageTrend(days),
  });
  const list = useQuery({
    queryKey: ["usage", "page", page, modelQ, taskQ, status, traceIdQ],
    queryFn: () =>
      usagePage({
        page,
        size: PAGE_SIZE,
        ...(modelQ ? { model: modelQ } : {}),
        ...(taskQ ? { task: taskQ } : {}),
        ...(status ? { status: status as "SUCCESS" | "ERROR" } : {}),
        ...(traceIdQ ? { trace_id: traceIdQ } : {}),
      }),
  });
  const timeline = useQuery({
    queryKey: ["usage", "timeline", timelineTrace],
    queryFn: () => usageTimeline(timelineTrace!),
    enabled: timelineTrace !== null,
  });

  // 筛选收窄致末页越界自愈(007/009/014 同款范式)。
  // R2-B2:筛选 onChange 已同步 setPage(1)(防抖值到达时 page 已归 1,
  // 单请求无「旧页一次+页 1 一次」双发窗口),此处不再挂回页 effect。
  useEffect(() => {
    if (list.data && list.data.rows.length === 0 && list.data.total > 0 && page > 1) {
      setPage((p) => Math.max(1, p - 1));
    }
  }, [list.data, page]);

  const totals = useMemo(() => {
    const items = summary.data?.items ?? [];
    const calls = items.reduce((a, i) => a + i.calls, 0);
    const errors = items.reduce((a, i) => a + i.calls_error, 0);
    const tokens = items.reduce((a, i) => a + i.total_tokens, 0);
    const latAvg: number[] = [];
    for (const i of items) {
      if (typeof i.latency_ms_avg === "number") {
        // 分组数不均,取组平均的近似口径(展示卡用途足够,精确值以明细行为准)
        latAvg.push(i.latency_ms_avg);
      }
    }
    return {
      calls,
      errors,
      tokens,
      errorRate: calls > 0 ? errors / calls : 0,
      latAvg: latAvg.length ? Math.round(latAvg.reduce((a, v) => a + v, 0) / latAvg.length) : null,
    };
  }, [summary.data]);

  const trendOption = useMemo(() => {
    const points = trend.data?.points ?? [];
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
        data: points.map((p) => p.date.slice(5)), // MM-DD 省宽
        ...axisCommon,
      },
      yAxis: [
        { type: "value" as const, minInterval: 1, ...axisCommon },
        { type: "value" as const, ...axisCommon },
      ],
      series: [
        {
          name: "调用",
          type: "line" as const,
          data: points.map((p) => p.calls),
          smooth: true,
          itemStyle: { color: cssVar("--ring") },
          lineStyle: { color: cssVar("--ring"), width: 2 },
        },
        {
          name: "失败",
          type: "line" as const,
          data: points.map((p) => p.calls_error),
          smooth: true,
          itemStyle: { color: cssVar("--primary") },
          lineStyle: { color: cssVar("--primary"), width: 2 },
        },
        {
          name: "tokens",
          type: "bar" as const,
          yAxisIndex: 1,
          data: points.map((p) => p.total_tokens),
          itemStyle: { color: cssVar("--accent-foreground"), opacity: 0.35 },
        },
      ],
    };
  }, [trend.data]);

  const totalPages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">模型用量</h2>
        <p className="text-sm text-muted-foreground">
          LLM 调用的用量、成败与延迟——看穿每一次问卦背后的模型开销。
        </p>
      </div>

      {/* 汇总卡 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="总调用"
          value={summary.isError ? undefined : String(totals.calls)}
          loading={summary.isLoading}
          error={summary.isError}
        />
        <StatCard
          label="总 tokens"
          value={summary.isError ? undefined : fmtTokens(totals.tokens)}
          loading={summary.isLoading}
          error={summary.isError}
        />
        <StatCard
          label="失败率"
          value={summary.isError ? undefined : `${(totals.errorRate * 100).toFixed(1)}%`}
          loading={summary.isLoading}
          error={summary.isError}
        />
        <StatCard
          label="平均延迟"
          value={summary.isError ? undefined : fmtMs(totals.latAvg)}
          loading={summary.isLoading}
          error={summary.isError}
        />
      </div>

      {/* 趋势 */}
      <div className="rounded-lg border bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-medium">按天趋势</h3>
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="h-8 w-24">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">近 7 天</SelectItem>
              <SelectItem value="30">近 30 天</SelectItem>
              <SelectItem value="90">近 90 天</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {trend.isLoading ? (
          <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">
            加载中…
          </div>
        ) : trend.isError ? (
          <div role="alert" className="flex h-56 items-center justify-center text-sm text-destructive">
            趋势加载失败
          </div>
        ) : (
          <ReactECharts option={trendOption} notMerge style={{ height: 240 }} />
        )}
      </div>

      {/* 模型×任务对比 */}
      <div className="rounded-lg border bg-card">
        <div className="border-b px-4 py-3">
          <h3 className="text-sm font-medium">模型 × 任务</h3>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>租户</TableHead>
              <TableHead>模型</TableHead>
              <TableHead>任务</TableHead>
              <TableHead className="text-right">调用</TableHead>
              <TableHead className="text-right">失败</TableHead>
              <TableHead className="text-right">tokens</TableHead>
              <TableHead className="text-right">平均延迟</TableHead>
              <TableHead className="text-right">最大延迟</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(summary.data?.items ?? []).map((i, idx) => (
              <TableRow key={idx}>
                <TableCell className="text-muted-foreground">{i.tenant_key}</TableCell>
                <TableCell className="font-medium">{i.model}</TableCell>
                <TableCell>{i.task}</TableCell>
                <TableCell className="text-right">{i.calls}</TableCell>
                <TableCell className="text-right">
                  {i.calls_error > 0 ? (
                    <Badge variant="destructive">{i.calls_error}</Badge>
                  ) : (
                    <span className="text-muted-foreground">0</span>
                  )}
                </TableCell>
                <TableCell className="text-right">{fmtTokens(i.total_tokens)}</TableCell>
                <TableCell className="text-right">{fmtMs(i.latency_ms_avg)}</TableCell>
                <TableCell className="text-right">{fmtMs(i.latency_ms_max)}</TableCell>
              </TableRow>
            ))}
            {!summary.isLoading && (summary.data?.items ?? []).length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                  {summary.isError ? "汇总加载失败" : "暂无用量记录"}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {/* 明细分页 */}
      <div className="rounded-lg border bg-card">
        <div className="flex flex-wrap items-end gap-3 border-b px-4 py-3">
          <div className="space-y-1">
            <Label className="text-xs">模型</Label>
            <Input
              className="h-8 w-36"
              placeholder="如 qwen-plus"
              maxLength={64}
              value={model}
              onChange={(e) => {
                setModel(e.target.value);
                setPage(1);
              }}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">任务</Label>
            <Input
              className="h-8 w-28"
              placeholder="如 SQL_GEN"
              maxLength={24}
              value={task}
              onChange={(e) => {
                setTask(e.target.value);
                setPage(1);
              }}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">状态</Label>
            <Select
              value={status || "ALL"}
              onValueChange={(v) => {
                setStatus(v === "ALL" ? "" : v);
                setPage(1);
              }}
            >
              <SelectTrigger className="h-8 w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">全部</SelectItem>
                <SelectItem value="SUCCESS">成功</SelectItem>
                <SelectItem value="ERROR">失败</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">trace_id</Label>
            <Input
              className="h-8 w-44"
              placeholder="精确匹配"
              maxLength={64}
              value={traceId}
              onChange={(e) => {
                setTraceId(e.target.value);
                setPage(1);
              }}
            />
          </div>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>时间</TableHead>
              <TableHead>租户</TableHead>
              <TableHead>模型</TableHead>
              <TableHead>任务</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">tokens</TableHead>
              <TableHead className="text-right">延迟</TableHead>
              <TableHead>trace</TableHead>
              <TableHead className="w-20"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(list.data?.rows ?? []).map((r) => (
              <TableRow key={r.id}>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {new Date(r.created_at).toLocaleString()}
                </TableCell>
                <TableCell className="text-muted-foreground">{r.tenant_key}</TableCell>
                <TableCell className="font-medium">{r.model}</TableCell>
                <TableCell>{r.task}</TableCell>
                <TableCell>
                  {r.status === "ERROR" ? (
                    <Badge variant="destructive">失败</Badge>
                  ) : (
                    <Badge variant="secondary">成功</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">{fmtTokens(r.total_tokens)}</TableCell>
                <TableCell className="text-right">{fmtMs(r.latency_ms)}</TableCell>
                <TableCell className="max-w-32 truncate font-mono text-xs text-muted-foreground">
                  {r.trace_id ?? "—"}
                </TableCell>
                <TableCell>
                  {r.trace_id && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2"
                      onClick={() => setTimelineTrace(r.trace_id ?? null)}
                    >
                      <Activity className="size-3.5" />
                      时间线
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {!list.isLoading && (list.data?.rows ?? []).length === 0 && (
              <TableRow>
                <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">
                  {list.isError ? "明细加载失败" : "无匹配记录"}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between border-t px-4 py-2 text-sm text-muted-foreground">
          <span>
            共 {list.data?.total ?? 0} 条 · 第 {page}/{totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </div>

      {/* 时间线对话框 */}
      <Dialog open={timelineTrace !== null} onOpenChange={(open) => !open && setTimelineTrace(null)}>
        <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Activity className="size-4" />
              trace 时间线
            </DialogTitle>
            <DialogDescription className="font-mono text-xs break-all">
              {timelineTrace}
            </DialogDescription>
          </DialogHeader>
          {timeline.isLoading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">加载中…</div>
          ) : timeline.isError ? (
            <div role="alert" className="py-8 text-center text-sm text-destructive">
              时间线加载失败
            </div>
          ) : (timeline.data?.events ?? []).length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              该 trace 下无事件
            </div>
          ) : (
            <ol className="space-y-2 border-l pl-4">
              {(timeline.data?.events ?? []).map((e, idx) => (
                <TimelineItem key={idx} event={e} />
              ))}
            </ol>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StatCard({
  label,
  value,
  loading,
  error,
}: {
  label: string;
  value: string | undefined;
  loading: boolean;
  error?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">
        {loading ? "…" : error ? "—" : (value ?? "—")}
      </div>
      {error && (
        <div role="alert" className="mt-0.5 text-xs text-destructive">
          加载失败
        </div>
      )}
    </div>
  );
}

function TimelineItem({ event }: { event: TimelineEvent }) {
  const Icon = KIND_ICON[event.kind] ?? Activity;
  const isErr =
    event.status === "ERROR" ||
    event.status === "REJECTED" ||
    event.status === "RATE_LIMITED" ||
    event.status === "CONFIRM_REJECTED" ||
    event.status === "CONFIRM_EXPIRED" ||
    event.status === "BLOCKED";
  const statusText = kindStatusLabel(event.kind, event.status);
  return (
    <li className="relative rounded-md border bg-card p-3 pl-9">
      <span
        className={`absolute left-[-1.35rem] top-3 flex size-5 items-center justify-center rounded-full border bg-card ${
          isErr ? "text-destructive" : "text-muted-foreground"
        }`}
      >
        <Icon className="size-3" />
      </span>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{event.name}</span>
        <Badge variant={isErr ? "destructive" : "secondary"}>{statusText}</Badge>
        {event.kind === "LLM" && typeof event.tokens === "number" && (
          <span className="text-xs text-muted-foreground">{fmtTokens(event.tokens)} tokens</span>
        )}
        {event.latency_ms !== null && (
          <span className="text-xs text-muted-foreground">{fmtMs(event.latency_ms)}</span>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {new Date(event.ts).toLocaleString()}
        </span>
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground">{event.kind}</div>
    </li>
  );
}
