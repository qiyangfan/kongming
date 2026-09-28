/* 轮次质量观测页(069 chat-quality-observability):线上真实质量第一天可观——
   汇总卡 + 按天趋势(补零)+ 数据源切片 + prompt 版本切片 + 轮次明细分页。
   NO_DATA 轮次首次拥有可见形态(此前无执行无审计行,任何观测面不可见);
   trace_id 点击跳转用量时间线抽屉(与 UsagePage 同源复用);
   图表色板从设计 token 派生(铁律 6);类型全生成(ADR-005);
   无 system:chat-quality:view 者接口层 403(nav/PermRoute 同源守卫)。 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Copy } from "lucide-react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { EChart, themedTooltip } from "@/lib/echarts";
import { useTheme } from "@/lib/theme-store";
import { DataView, type DataViewAction } from "@/components/data-view";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { ErrorAlert } from "@/components/error-alert";
import { usageTimeline, type TimelineEvent } from "@/lib/usage-api";
import {
  qualityPage,
  qualitySummary,
  type QualityRoundItemOut,
  type QualityTrendPoint,
} from "@/lib/quality-api";
import { formatDateTime } from "@/lib/datetime";
import { cssVar } from "@/lib/utils";

const PAGE_SIZE = 20;

/* 轮次四态徽章(与 audit/usage 同范式:值 → 徽章统一走 StatusBadge;
   044 NO_DATA=知识未覆盖中性形态,不用失败色) */
const ROUND_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  NO_DATA: { label: "未覆盖", variant: "outline" },
  REJECTED: { label: "已拦截", variant: "destructive" },
  FAILED: { label: "失败", variant: "destructive" },
};

/* 按天趋势(theme 经 key 作用于本组件:切主题整体重挂,option 重读 token 色板) */
function QualityTrendChart({ points }: { points: QualityTrendPoint[] }) {
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
      yAxis: { type: "value" as const, minInterval: 1, ...axisCommon },
      series: [
        {
          name: "轮次",
          type: "line" as const,
          data: points.map((p) => p.rounds),
          smooth: true,
          itemStyle: { color: cssVar("--ring") },
          lineStyle: { color: cssVar("--ring"), width: 2 },
        },
        {
          name: "成功",
          type: "line" as const,
          data: points.map((p) => p.success),
          smooth: true,
          itemStyle: { color: cssVar("--primary") },
          lineStyle: { color: cssVar("--primary"), width: 2 },
        },
        {
          name: "未覆盖",
          type: "bar" as const,
          data: points.map((p) => p.no_data),
          itemStyle: { color: cssVar("--accent-foreground"), opacity: 0.35 },
        },
      ],
    };
  }, [points]);
  return <EChart option={option} notMerge style={{ height: 240 }} />;
}

/** 时间线抽屉(trace_id 跳转;与 UsagePage 复用同一 usageTimeline 端点) */
function TimelineDialog({ traceId, onClose }: { traceId: string | null; onClose: () => void }) {
  const timeline = useQuery({
    queryKey: ["usage", "timeline", traceId],
    queryFn: () => usageTimeline(traceId!),
    enabled: traceId !== null,
  });
  const events: TimelineEvent[] = timeline.data?.events ?? [];
  return (
    <Dialog open={traceId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[70vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>轮次时间线</DialogTitle>
          <DialogDescription>
            <span className="font-mono text-xs">{traceId}</span>
          </DialogDescription>
        </DialogHeader>
        {timeline.isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>
        ) : timeline.isError ? (
          <ErrorAlert text="时间线加载失败" />
        ) : events.length === 0 ? (
          <EmptyState icon={BookOpen}>
            <p className="font-medium text-foreground">暂无时间线事件</p>
            <p>该 trace_id 无 LLM/工具/SQL 事件记录。</p>
          </EmptyState>
        ) : (
          <ol className="space-y-2">
            {events.map((e, i) => (
              <li key={`${e.ts}-${i}`} className="flex items-center justify-between gap-2 text-sm">
                <span className="text-muted-foreground">
                  {formatDateTime(e.ts, { seconds: true })}
                </span>
                <span className="truncate font-medium">
                  {e.kind} · {e.name}
                </span>
                <StatusBadge value={e.status} meta={TIMELINE_STATUS_META} />
              </li>
            ))}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  );
}

/* 时间线事件状态徽章(LLM/TOOL/SQL 三路;值集开放,fallback 兜原值) */
const TIMELINE_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  EXECUTED: { label: "已执行", variant: "secondary" },
  PASS: { label: "通过", variant: "secondary" },
  DONE: { label: "完成", variant: "secondary" },
  ERROR: { label: "失败", variant: "destructive" },
  REJECTED: { label: "已拒绝", variant: "destructive" },
  BLOCKED: { label: "拦截", variant: "destructive" },
};

const pct = (v: number): string => `${(v * 100).toFixed(1)}%`;

export default function QualityPage() {
  const { theme } = useTheme();
  const navigate = useNavigate();
  const [days, setDays] = useState(30);
  const [status, setStatus] = useState("");
  const [datasourceKey, setDatasourceKey] = useState("");
  const [page, setPage] = useState(1);
  const [timelineTrace, setTimelineTrace] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const summary = useQuery({
    queryKey: ["quality", "summary", days],
    queryFn: () => qualitySummary(days),
  });
  const list = useQuery({
    queryKey: ["quality", "page", page, status, datasourceKey],
    queryFn: () =>
      qualityPage({
        page,
        size: PAGE_SIZE,
        ...(status ? { status: status as "SUCCESS" | "FAILED" | "REJECTED" | "NO_DATA" } : {}),
        ...(datasourceKey ? { datasource_key: datasourceKey } : {}),
      }),
  });

  // 筛选收窄致末页越界自愈(007/009/014 同款范式;onChange 已同步 setPage(1))
  useEffect(() => {
    if (list.data && list.data.items.length === 0 && list.data.total > 0 && page > 1) {
      setPage((p) => Math.max(1, p - 1));
    }
  }, [list.data, page]);

  // 040·11:筛选候选自切片行派生(数据源维度;本页已在拉)
  const dsOptions = useMemo(
    () => (summary.data?.by_datasource ?? []).map((r) => r.datasource_key).sort(),
    [summary.data],
  );

  const totalPages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE));
  const ov = summary.data?.overview;
  const stat = (v: string) => (summary.isLoading ? "…" : summary.isError ? "—" : v);

  const rowActions: DataViewAction<QualityRoundItemOut>[] = [
    {
      label: "复制问题",
      onSelect: (row) => {
        const text = row.question ?? "";
        void navigator.clipboard.writeText(text);
        setCopied(row.id);
      },
      isVisible: (row) => Boolean(row.question),
    },
    {
      label: "知识库补全",
      onSelect: () => navigate("/admin/rag"),
      // NO_DATA 轮次的知识补全闭环最小操作面(原 turn-status 行动作并入此处)
      isVisible: (row) => row.status === "NO_DATA",
    },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title="问答质量"
        description="线上每一轮的四态占比、未覆盖分布与 prompt 版本切片——真实质量,不再靠猜。"
      />

      {/* 汇总卡(卡级错误不逐卡 role=alert,数值降 …/— 示错) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="总轮次" value={stat(String(ov?.total ?? 0))} />
        <StatCard label="成功率" value={stat(ov ? pct(ov.success / (ov.total || 1)) : "0.0%")} />
        <StatCard label="未覆盖率" value={stat(ov ? pct(ov.no_data_rate) : "0.0%")} />
        <StatCard label="自愈率" value={stat(ov ? pct(ov.selfheal_rate) : "0.0%")} />
      </div>

      {/* 趋势 */}
      <div className="rounded-lg border bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-medium">按天趋势</h3>
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
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
        {summary.isLoading ? (
          <div className="flex h-56 items-center justify-center text-sm text-muted-foreground">
            加载中…
          </div>
        ) : summary.isError ? (
          <ErrorAlert text="汇总加载失败" />
        ) : (
          <QualityTrendChart key={theme} points={summary.data?.trend ?? []} />
        )}
      </div>

      {/* 数据源 × prompt 版本切片(纯展示表:不传 actions) */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <h3 className="text-sm font-medium">数据源切片</h3>
          </div>
          <DataView
            rows={summary.data?.by_datasource ?? []}
            rowKey={(r) => r.datasource_key}
            isLoading={summary.isLoading}
            isEmpty={!summary.isLoading && !summary.isError && !summary.data?.by_datasource.length}
            emptyText="暂无轮次记录"
            columns={[
              { key: "datasource_key", header: "数据源", cardPriority: 1 },
              { key: "rounds", header: "轮次", cellClassName: "text-right" },
              { key: "success", header: "成功", cellClassName: "text-right" },
              { key: "no_data", header: "未覆盖", cellClassName: "text-right" },
              { key: "blocked", header: "拦截", cellClassName: "text-right" },
              { key: "failed", header: "失败", cellClassName: "text-right" },
            ]}
          />
        </div>
        <div className="rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <h3 className="text-sm font-medium">prompt 版本切片</h3>
          </div>
          <DataView
            rows={summary.data?.by_prompt_version ?? []}
            rowKey={(r) => r.prompt_version}
            isLoading={summary.isLoading}
            isEmpty={
              !summary.isLoading && !summary.isError && !summary.data?.by_prompt_version.length
            }
            emptyText="暂无轮次记录"
            columns={[
              { key: "prompt_version", header: "版本", cardPriority: 1 },
              { key: "rounds", header: "轮次", cellClassName: "text-right" },
              { key: "success", header: "成功", cellClassName: "text-right" },
              { key: "no_data", header: "未覆盖", cellClassName: "text-right" },
              { key: "blocked", header: "拦截", cellClassName: "text-right" },
              { key: "failed", header: "失败", cellClassName: "text-right" },
            ]}
          />
        </div>
      </div>

      {/* 轮次明细(状态/数据源筛选;trace_id 跳转时间线) */}
      <div className="rounded-lg border bg-card">
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <h3 className="mr-auto text-sm font-medium">轮次明细</h3>
          <Select
            value={status || "all"}
            onValueChange={(v) => {
              setStatus(v === "all" ? "" : v);
              setPage(1);
            }}
          >
            <SelectTrigger size="sm" className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="SUCCESS">成功</SelectItem>
              <SelectItem value="NO_DATA">未覆盖</SelectItem>
              <SelectItem value="REJECTED">已拦截</SelectItem>
              <SelectItem value="FAILED">失败</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={datasourceKey || "all"}
            onValueChange={(v) => {
              setDatasourceKey(v === "all" ? "" : v);
              setPage(1);
            }}
          >
            <SelectTrigger size="sm" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部数据源</SelectItem>
              {dsOptions.map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {!list.isLoading && list.isError && (
          <div className="px-4 py-3">
            <ErrorAlert text="明细加载失败" />
          </div>
        )}
        <DataView
          rows={list.data?.items ?? []}
          rowKey={(r) => r.id}
          actions={rowActions}
          isLoading={list.isLoading}
          isEmpty={!list.isLoading && !list.isError && !list.data?.items.length}
          emptyText="暂无轮次记录"
          columns={[
            {
              key: "question",
              header: "问题",
              cardPriority: 1,
              render: (r) => (
                <div className="max-w-64">
                  <span className="block truncate" title={r.question ?? ""}>
                    {r.question ?? "—"}
                  </span>
                  {/* 多轮改写结果(仅实际改写时存在):诊断信号,弱化呈现 */}
                  {r.resolved_question && (
                    <span
                      className="block truncate text-xs text-muted-foreground"
                      title={r.resolved_question}
                    >
                      改写:{r.resolved_question}
                    </span>
                  )}
                </div>
              ),
            },
            {
              key: "status",
              header: "状态",
              cardPriority: 2,
              render: (r) => (
                <span className="inline-flex items-center gap-1.5">
                  <StatusBadge value={r.status} meta={ROUND_STATUS_META} />
                  {r.retry_count > 0 && (
                    <span className="text-xs text-muted-foreground">重试 {r.retry_count}</span>
                  )}
                </span>
              ),
            },
            {
              key: "rating",
              header: "评分",
              render: (r) => (r.rating === "UP" ? "赞" : r.rating === "DOWN" ? "踩" : "—"),
            },
            {
              key: "datasource_key",
              header: "数据源",
              cellClassName: "text-muted-foreground",
            },
            {
              key: "prompt_version",
              header: "prompt",
              cellClassName: "text-muted-foreground",
            },
            {
              key: "created_at",
              header: "时间",
              render: (r) => formatDateTime(r.created_at),
            },
            {
              key: "trace_id",
              header: "链路",
              render: (r) =>
                r.trace_id ? (
                  <Button
                    variant="link"
                    size="xs"
                    className="h-auto font-mono p-0 text-xs"
                    onClick={() => setTimelineTrace(r.trace_id ?? null)}
                  >
                    {r.trace_id.slice(0, 8)}…
                  </Button>
                ) : (
                  "—"
                ),
            },
          ]}
        />
        {copied && (
          <p className="px-4 pb-2 text-xs text-muted-foreground" role="status">
            <Copy className="mr-1 inline size-3" aria-hidden />
            已复制问题到剪贴板
          </p>
        )}
        {totalPages > 1 && (
          <div className="border-t px-4 py-3">
            <Pagination
              page={page}
              pageCount={totalPages}
              total={list.data?.total}
              onPageChange={setPage}
            />
          </div>
        )}
      </div>

      <TimelineDialog traceId={timelineTrace} onClose={() => setTimelineTrace(null)} />
      <span className="hidden" aria-hidden>
        <BookOpen aria-hidden />
      </span>
    </div>
  );
}
