/* 用量观测页(038 agent-observability):LLM 调用/失败/延迟的可观测面板——
   汇总卡 + 按天趋势(双轴:调用/失败 vs tokens)+ 模型×任务对比表 +
   明细分页(四类筛选)+ trace 时间线对话框(LLM/工具/SQL 三路事件流)。
   图表色板从设计 token 派生(铁律 6);类型全生成(ADR-005);
   无 system:llm-usage:view 者接口层 403(nav/PermRoute 同源守卫)。 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Database, Sparkles, Wrench } from "lucide-react";

import { EChart, themedTooltip } from "@/lib/echarts";
import { useTheme } from "@/lib/theme-store";
import { Badge } from "@/components/ui/badge";
import { DataView } from "@/components/data-view";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/empty-state";
import { Pagination } from "@/components/pagination";
import { StatCard } from "@/components/stat-card";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  usagePage,
  usageSummary,
  usageTimeline,
  usageTrend,
  type TimelineEvent,
} from "@/lib/usage-api";

import { useDebounced } from "@/hooks/use-debounced";
import { ErrorAlert } from "@/components/error-alert";
import { systemApi } from "@/lib/system-api";
import { cssVar } from "@/lib/utils";
import { formatDateTime, formatDuration } from "@/lib/datetime";

const PAGE_SIZE = 20;

function fmtTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 10_000) return `${(v / 1000).toFixed(1)}k`;
  return String(v);
}

const KIND_ICON = { LLM: Sparkles, TOOL: Wrench, SQL: Database } as const;

// 事件类型中文(时间线黑话直出克星);未收录值回退原值
const KIND_LABELS: Record<string, string> = {
  LLM: "模型调用",
  TOOL: "工具",
  SQL: "SQL",
};

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

// 明细表状态列(值 → 徽章统一走 StatusBadge)
const LLM_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  ERROR: { label: "失败", variant: "destructive" },
};

function kindStatusLabel(kind: string, status: string): string {
  const table =
    kind === "TOOL" ? TOOL_STATUS_LABEL : kind === "SQL" ? VERDICT_LABEL : LLM_STATUS_LABEL;
  return table[status] ?? status;
}

type UsageTrendPoint = Awaited<ReturnType<typeof usageTrend>>["points"][number];

/* 按天趋势(theme 经 key 作用于本组件:切主题整体重挂,option 重读 token 色板) */
function UsageTrendChart({ points }: { points: UsageTrendPoint[] }) {
  const option = useMemo(() => {
    const axisColor = cssVar("--muted-foreground");
    const lineColor = cssVar("--border");
    const axisCommon = {
      axisLabel: { color: axisColor },
      axisLine: { lineStyle: { color: lineColor } },
    };
    return {
      tooltip: { trigger: "axis" as const, ...themedTooltip() },
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
          name: "Token",
          type: "bar" as const,
          yAxisIndex: 1,
          data: points.map((p) => p.total_tokens),
          itemStyle: { color: cssVar("--accent-foreground"), opacity: 0.35 },
        },
      ],
    };
  }, [points]);
  return <EChart option={option} notMerge style={{ height: 240 }} />;
}

export default function UsagePage() {
  const { theme } = useTheme();
  const [days, setDays] = useState(30);
  const [model, setModel] = useState("");
  const [task, setTask] = useState("");
  const [status, setStatus] = useState("");
  const [traceId, setTraceId] = useState("");
  const [tenantId, setTenantId] = useState("");
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
  const tenants = useQuery({
    queryKey: ["admin", "tenants"],
    queryFn: systemApi.listTenants,
  });
  const list = useQuery({
    queryKey: ["usage", "page", page, modelQ, taskQ, status, traceIdQ, tenantId],
    queryFn: () =>
      usagePage({
        page,
        size: PAGE_SIZE,
        ...(modelQ ? { model: modelQ } : {}),
        ...(taskQ ? { task: taskQ } : {}),
        ...(status ? { status: status as "SUCCESS" | "ERROR" } : {}),
        ...(traceIdQ ? { trace_id: traceIdQ } : {}),
        ...(tenantId ? { tenant_id: tenantId } : {}),
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

  // 040·11:筛选候选自 summary 维度行派生(去重排序;summary 本页已在拉)
  const modelOptions = useMemo(
    () => Array.from(new Set((summary.data?.items ?? []).map((i) => i.model))).sort(),
    [summary.data],
  );
  const taskOptions = useMemo(
    () => Array.from(new Set((summary.data?.items ?? []).map((i) => i.task))).sort(),
    [summary.data],
  );

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

  const totalPages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE));

  /** 汇总卡数值加载/错误态折叠(R1-B3:loading 显 …、错误显 —,不逐卡 role=alert) */
  const usageStat = (v: string) => (summary.isLoading ? "…" : summary.isError ? "—" : v);

  return (
    <div className="space-y-4">
      <PageHeader
        title="模型用量"
        description="模型调用的用量、成败与延迟——看穿每一次问卦背后的开销。"
      />

      {/* 汇总卡(R1-B3:卡级错误不逐卡 role=alert,数值降 …/— 示错;区块级错误条由消费区承载) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="总调用" value={usageStat(String(totals.calls))} />
        <StatCard label="Tokens 总量" value={usageStat(fmtTokens(totals.tokens))} />
        <StatCard label="失败率" value={usageStat(`${(totals.errorRate * 100).toFixed(1)}%`)} />
        <StatCard label="平均延迟" value={usageStat(formatDuration(totals.latAvg))} />
      </div>

      {/* 趋势 */}
      <div className="rounded-lg border bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-medium">按天趋势</h3>
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            {/* w-28 下限:装下最宽项「近 30 天」(w-24 时文本预算 48px<53px 被裁) */}
            <SelectTrigger size="sm" className="w-28">
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
          <ErrorAlert text="趋势加载失败" />
        ) : (
          <UsageTrendChart key={theme} points={trend.data?.points ?? []} />
        )}
      </div>

      {/* 模型×任务对比 */}
      <div className="rounded-lg border bg-card">
        <div className="border-b px-4 py-3">
          <h3 className="text-sm font-medium">模型 × 任务</h3>
        </div>
        {/* 汇总加载失败:错误条置于表区上方,DataView 只管加载/空态 */}
        {!summary.isLoading && summary.isError && (
          <div className="px-4 py-3">
            <ErrorAlert text="汇总加载失败" />
          </div>
        )}
        {/* 纯展示表(无行操作):不传 actions;rowKey 取分组维度三元组 */}
        <DataView
          rows={summary.data?.items ?? []}
          rowKey={(i) => `${i.tenant_key}-${i.model}-${i.task}`}
          isLoading={summary.isLoading}
          isEmpty={!summary.isLoading && !summary.isError && summary.data?.items.length === 0}
          emptyText="暂无用量记录"
          columns={[
            {
              key: "tenant_key",
              header: "租户",
              cellClassName: "text-muted-foreground",
            },
            {
              key: "model",
              header: "模型",
              cardPriority: 1,
              render: (i) => (
                <span className="block max-w-40 truncate font-medium" title={i.model}>
                  {i.model}
                </span>
              ),
            },
            { key: "task", header: "任务", cardPriority: 2 },
            {
              key: "calls",
              header: "调用",
              cardPriority: 3,
              cellClassName: "text-right",
              render: (i) => i.calls,
            },
            {
              key: "calls_error",
              header: "失败",
              cardPriority: 4,
              cellClassName: "text-right",
              render: (i) =>
                i.calls_error > 0 ? (
                  <Badge variant="destructive">{i.calls_error}</Badge>
                ) : (
                  <span className="text-muted-foreground">0</span>
                ),
            },
            {
              key: "total_tokens",
              header: "Tokens",
              cardPriority: 5,
              cellClassName: "text-right",
              render: (i) => fmtTokens(i.total_tokens),
            },
            {
              key: "latency_ms_avg",
              header: "平均延迟",
              cellClassName: "text-right",
              render: (i) => formatDuration(i.latency_ms_avg),
            },
            {
              key: "latency_ms_max",
              header: "最大延迟",
              cellClassName: "text-right",
              render: (i) => formatDuration(i.latency_ms_max),
            },
          ]}
        />
      </div>

      {/* 明细分页 */}
      <div className="rounded-lg border bg-card">
        <div className="flex flex-wrap items-end gap-3 border-b px-4 py-3">
          {/* 040·11:模型/任务筛选换 Select——候选自既有 summary 响应派生
              (每行自带 model/task 维度,纯前端零契约),消灭手拼精确匹配串 */}
          <div className="space-y-1">
            <Label className="text-xs">模型</Label>
            <Select
              value={model || "ALL"}
              onValueChange={(v) => {
                setModel(v === "ALL" ? "" : v);
                setPage(1);
              }}
            >
              <SelectTrigger size="sm" className="w-40" aria-label="模型筛选">
                <SelectValue placeholder="全部模型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">全部模型</SelectItem>
                {modelOptions.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">任务</Label>
            <Select
              value={task || "ALL"}
              onValueChange={(v) => {
                setTask(v === "ALL" ? "" : v);
                setPage(1);
              }}
            >
              <SelectTrigger size="sm" className="w-36" aria-label="任务筛选">
                <SelectValue placeholder="全部任务" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">全部任务</SelectItem>
                {taskOptions.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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
              <SelectTrigger size="sm" className="w-24">
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
            <Label htmlFor="usage-tenant-filter" className="text-xs">
              租户
            </Label>
            <Select
              value={tenantId || "ALL"}
              onValueChange={(v) => {
                setTenantId(v === "ALL" ? "" : v);
                setPage(1);
              }}
              disabled={tenants.isError}
            >
              <SelectTrigger id="usage-tenant-filter" size="sm" className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">全部</SelectItem>
                {(tenants.data ?? []).map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.tenant_key}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {/* R1-F2:listTenants 挂 user:manage(页面守卫是 llm-usage:view,
                权限错配时 403)——失败显式禁用+提示,不静默 */}
            {tenants.isError && (
              <p role="alert" className="text-xs text-destructive">
                租户筛选不可用
              </p>
            )}
          </div>
          <div className="space-y-1">
            <Label className="text-xs">链路 ID</Label>
            <Input
              size="sm"
              className="w-44"
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
        {/* 明细加载失败:错误条置于表区上方,DataView 只管加载/空态 */}
        {!list.isLoading && list.isError && (
          <div className="px-4 py-3">
            <ErrorAlert text="明细加载失败" />
          </div>
        )}
        <DataView
          rows={list.data?.rows ?? []}
          rowKey={(r) => r.id}
          isLoading={list.isLoading}
          isEmpty={!list.isLoading && !list.isError && list.data?.rows.length === 0}
          emptyText="无匹配记录"
          columns={[
            {
              key: "created_at",
              header: "时间",
              cardPriority: 2,
              cellClassName: "whitespace-nowrap text-muted-foreground",
              render: (r) => formatDateTime(r.created_at),
            },
            {
              key: "tenant_key",
              header: "租户",
              cellClassName: "text-muted-foreground",
            },
            {
              key: "model",
              header: "模型",
              cardPriority: 1,
              render: (r) => (
                <span className="block max-w-40 truncate font-medium" title={r.model}>
                  {r.model}
                </span>
              ),
            },
            { key: "task", header: "任务", cardPriority: 4 },
            {
              key: "status",
              header: "状态",
              cardPriority: 3,
              render: (r) => <StatusBadge meta={LLM_STATUS_META} value={r.status} />,
            },
            {
              key: "total_tokens",
              header: "Tokens",
              cardPriority: 5,
              cellClassName: "text-right",
              render: (r) => fmtTokens(r.total_tokens),
            },
            {
              key: "latency_ms",
              header: "延迟",
              cellClassName: "text-right",
              render: (r) => formatDuration(r.latency_ms),
            },
            {
              key: "trace_id",
              header: "链路",
              render: (r) => (
                <span
                  className="block max-w-32 truncate font-mono text-xs text-muted-foreground"
                  title={r.trace_id ?? undefined}
                >
                  {r.trace_id ?? "—"}
                </span>
              ),
            },
          ]}
          actions={[
            {
              label: "时间线",
              // 与原条件渲染同口径:无 trace_id 的行不渲染该操作
              isVisible: (r) => Boolean(r.trace_id),
              onSelect: (r) => {
                if (r.trace_id) setTimelineTrace(r.trace_id);
              },
            },
          ]}
        />
        <Pagination
          page={page}
          pageCount={totalPages}
          total={list.data?.total ?? 0}
          onPageChange={setPage}
          className="border-t px-4 py-2"
        />
      </div>

      {/* 时间线对话框 */}
      <Dialog
        open={timelineTrace !== null}
        onOpenChange={(open) => !open && setTimelineTrace(null)}
      >
        <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Activity className="size-4" />
              调用链路
            </DialogTitle>
            <DialogDescription
              className="max-w-full truncate font-mono text-xs"
              title={timelineTrace ?? undefined}
            >
              {timelineTrace}
            </DialogDescription>
          </DialogHeader>
          {timeline.isLoading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">加载中…</div>
          ) : timeline.isError ? (
            <ErrorAlert text="时间线加载失败" />
          ) : (timeline.data?.events ?? []).length === 0 ? (
            <EmptyState>该链路暂无事件</EmptyState>
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
        className={`absolute -left-5.5 top-3 flex size-5 items-center justify-center rounded-full border bg-card ${
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
          <span className="text-xs text-muted-foreground">{formatDuration(event.latency_ms)}</span>
        )}
        <span className="ml-auto text-xs text-muted-foreground">{formatDateTime(event.ts)}</span>
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground">
        {KIND_LABELS[event.kind] ?? event.kind}
      </div>
    </li>
  );
}
