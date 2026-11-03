/* 展示问答轮次的汇总、趋势、切片和明细；NO_DATA 单独呈现，trace_id 可打开用量时间线。 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Copy, Download, GitBranch } from "lucide-react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { EChart, themedTooltip } from "@/lib/echarts";
import { useTheme } from "@/lib/theme-store";
import { STAGE_LABELS } from "@/components/stream-card";
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
import { api } from "@/lib/api";
import {
  qualityContext,
  qualityExportUrl,
  qualityPage,
  qualityScoreDetail,
  qualityStageTrace,
  qualitySummary,
  type QualityContextItemOut,
  type QualityRoundItemOut,
  type QualityTrendPoint,
} from "@/lib/quality-api";
import { formatDateTime } from "@/lib/datetime";
import { cssVar } from "@/lib/utils";

const PAGE_SIZE = 20;

/* 评分分解结构(后端 dict[str,object];防御性读取,畸形字段按缺省呈现) */
interface ScoreClaim {
  text?: string;
  importance?: string;
  support?: string;
  excerpt?: string;
}
interface ScoreDecomposition {
  claims?: ScoreClaim[];
  excluded_claims?: ScoreClaim[];
  points_total?: number;
  points_covered?: number;
}

/* 主张重要度/支撑分级徽章口径(与后端公式取值域同源) */
const IMPORTANCE_META: StatusMeta = {
  critical: { label: "核心", variant: "destructive" },
  normal: { label: "重要", variant: "secondary" },
  minor: { label: "补充", variant: "outline" },
};
const SUPPORT_META: StatusMeta = {
  fully_supported: { label: "有据", variant: "secondary" },
  partially_supported: { label: "部分有据", variant: "outline" },
  unsupported: { label: "无据", variant: "destructive" },
};
/* 兜底轮分类徽章(spec「兜底轮分类观测」;空=非兜底轮) */
const FALLBACK_META: StatusMeta = {
  CHITCHAT: { label: "闲聊", variant: "outline" },
  CLARIFY: { label: "澄清", variant: "outline" },
};

/* 轮次四态徽章;NO_DATA 是知识未覆盖的中性形态,不用失败色 */
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

/* 时间线事件状态徽章;值集开放,未收录值回退原值 */
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

/** 同会话轮次串联面板(spec「同会话轮次串联」;锚点轮必含,升序) */
function ContextDialog({ messageId, onClose }: { messageId: string | null; onClose: () => void }) {
  const ctx = useQuery({
    queryKey: ["quality", "context", messageId],
    queryFn: () => qualityContext(messageId!),
    enabled: messageId !== null,
  });
  const items: QualityContextItemOut[] = ctx.data?.items ?? [];
  return (
    <Dialog open={messageId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[70vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>同会话轮次</DialogTitle>
          <DialogDescription>
            <span className="font-mono text-xs">{ctx.data?.session_id ?? ""}</span>
          </DialogDescription>
        </DialogHeader>
        {ctx.isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>
        ) : ctx.isError ? (
          <ErrorAlert text="会话轮次加载失败" />
        ) : (
          <ol className="space-y-2">
            {items.map((it) => (
              <li
                key={it.id}
                className={`rounded-lg border p-2.5 text-sm ${
                  it.id === messageId ? "border-primary bg-primary/5" : ""
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs text-muted-foreground">
                    {formatDateTime(it.created_at, { seconds: true })}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <StatusBadge value={it.status} meta={ROUND_STATUS_META} />
                    {it.fallback_type && (
                      <StatusBadge value={it.fallback_type} meta={FALLBACK_META} />
                    )}
                    {it.score != null && (
                      <span className="font-mono text-xs">{it.score} 分</span>
                    )}
                  </span>
                </div>
                <p className="mt-1 line-clamp-2">{it.question ?? "—"}</p>
              </li>
            ))}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 节点阶段轨迹抽屉(spec「节点阶段轨迹持久化」;null=同步轮如实为空) */
function StageTraceDialog({ messageId, onClose }: { messageId: string | null; onClose: () => void }) {
  const trace = useQuery({
    queryKey: ["quality", "stage-trace", messageId],
    queryFn: () => qualityStageTrace(messageId!),
    enabled: messageId !== null,
  });
  const events = trace.data?.stage_trace ?? null;
  const truncated = events?.some((e) => "truncated" in e);
  const nodes = (events ?? []).filter((e) => "node" in e);
  return (
    <Dialog open={messageId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[70vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>执行轨迹</DialogTitle>
          <DialogDescription>该轮各节点的执行序列与相对耗时(ms)。</DialogDescription>
        </DialogHeader>
        {trace.isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>
        ) : trace.isError ? (
          <ErrorAlert text="轨迹加载失败" />
        ) : nodes.length === 0 ? (
          <EmptyState icon={GitBranch}>
            <p className="font-medium text-foreground">无轨迹</p>
            <p>同步问答轮不产生节点阶段轨迹。</p>
          </EmptyState>
        ) : (
          <>
            {truncated && (
              <p className="text-xs text-warning">条目超上限,仅保留最近记录(已截断)。</p>
            )}
            <ol className="space-y-1.5">
              {nodes.map((e, i) => {
                const node = String((e as Record<string, unknown>).node ?? "");
                const status = String((e as Record<string, unknown>).status ?? "");
                const t = Number((e as Record<string, unknown>).t ?? 0);
                const retry = Number((e as Record<string, unknown>).retry ?? 0);
                return (
                  <li
                    key={i}
                    className="flex items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-sm"
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <StatusBadge
                        value={status}
                        meta={{
                          running: { label: "进行中", variant: "outline" },
                          done: { label: "完成", variant: "secondary" },
                        }}
                      />
                      <span className="font-medium">{STAGE_LABELS[node] ?? node}</span>
                      {node === "selfheal" && retry > 0 && (
                        <span className="text-xs text-muted-foreground">第 {retry} 轮</span>
                      )}
                    </span>
                    <span className="font-mono text-xs text-muted-foreground">{t} ms</span>
                  </li>
                );
              })}
            </ol>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 评分详情抽屉(维度分+主张分解;404=未评分或越权,同形提示) */
function ScoreDetailDialog({ messageId, onClose }: { messageId: string | null; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["quality", "score", messageId],
    queryFn: () => qualityScoreDetail(messageId!),
    enabled: messageId !== null,
  });
  const dec = (detail.data?.decomposition ?? {}) as ScoreDecomposition;
  const claims = dec.claims ?? [];
  const excluded = dec.excluded_claims ?? [];
  return (
    <Dialog open={messageId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[70vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>答案评分详情</DialogTitle>
          <DialogDescription>
            分数由主张分解经确定性公式重算;每条扣分可定位到具体主张。
          </DialogDescription>
        </DialogHeader>
        {detail.isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>
        ) : detail.isError ? (
          <ErrorAlert text="评分详情加载失败(可能未评分或无权查看)" />
        ) : detail.data ? (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-3 text-center">
              <div className="rounded-lg border bg-muted/40 p-3">
                <p className="text-2xl font-semibold">{detail.data.score}</p>
                <p className="text-xs text-muted-foreground">总分(0-100)</p>
              </div>
              <div className="rounded-lg border bg-muted/40 p-3">
                <p className="text-2xl font-semibold">{detail.data.grounding}</p>
                <p className="text-xs text-muted-foreground">依据分</p>
              </div>
              <div className="rounded-lg border bg-muted/40 p-3">
                <p className="text-2xl font-semibold">{detail.data.coverage}</p>
                <p className="text-xs text-muted-foreground">覆盖分</p>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              要点覆盖:{dec.points_covered ?? 0}/{dec.points_total ?? 0}
              {detail.data.score_model && ` · 评审模型 ${detail.data.score_model}`}
              {` · ${formatDateTime(detail.data.scored_at)}`}
            </p>
            <div className="space-y-2">
              <h4 className="text-sm font-medium">事实主张({claims.length})</h4>
              {claims.map((c, i) => (
                <div key={i} className="rounded-lg border p-3 text-sm">
                  <div className="mb-1.5 flex items-center gap-1.5">
                    <StatusBadge value={c.importance ?? "normal"} meta={IMPORTANCE_META} />
                    <StatusBadge value={c.support ?? "unsupported"} meta={SUPPORT_META} />
                  </div>
                  <p>{c.text}</p>
                  {c.excerpt && (
                    <p className="mt-1.5 border-l-2 pl-2 text-xs text-muted-foreground">
                      证据摘录:{c.excerpt}
                    </p>
                  )}
                </div>
              ))}
            </div>
            {excluded.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-sm font-medium text-muted-foreground">
                  净化剔除的非事实主张({excluded.length},不参与依据分)
                </h4>
                {excluded.map((c, i) => (
                  <p key={i} className="text-xs text-muted-foreground">
                    · {c.text}
                  </p>
                ))}
              </div>
            )}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export default function QualityPage() {
  const { theme } = useTheme();
  const navigate = useNavigate();
  const [days, setDays] = useState(30);
  const [status, setStatus] = useState("");
  const [datasourceKey, setDatasourceKey] = useState("");
  const [fallback, setFallback] = useState("");
  const [page, setPage] = useState(1);
  const [timelineTrace, setTimelineTrace] = useState<string | null>(null);
  const [scoreMessageId, setScoreMessageId] = useState<string | null>(null);
  const [contextMessageId, setContextMessageId] = useState<string | null>(null);
  const [stageTraceId, setStageTraceId] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const summary = useQuery({
    queryKey: ["quality", "summary", days],
    queryFn: () => qualitySummary(days),
  });
  const list = useQuery({
    queryKey: ["quality", "page", page, status, datasourceKey, fallback],
    queryFn: () =>
      qualityPage({
        page,
        size: PAGE_SIZE,
        ...(status ? { status: status as "SUCCESS" | "FAILED" | "REJECTED" | "NO_DATA" } : {}),
        ...(datasourceKey ? { datasource_key: datasourceKey } : {}),
        ...(fallback
          ? { fallback: fallback as "CHITCHAT" | "CLARIFY" | "none" }
          : {}),
      }),
  });

  // 当前页被清空时退回上一页,避免落在空页。
  useEffect(() => {
    if (list.data && list.data.items.length === 0 && list.data.total > 0 && page > 1) {
      setPage((p) => Math.max(1, p - 1));
    }
  }, [list.data, page]);

  // 数据源筛选项从已加载的切片数据中生成。
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
      label: "同会话轮次",
      onSelect: (row) => setContextMessageId(row.id),
    },
    {
      label: "执行轨迹",
      onSelect: (row) => setStageTraceId(row.id),
    },
    {
      label: "知识库补全",
      onSelect: () => navigate("/admin/rag"),
      isVisible: (row) => row.status === "NO_DATA",
    },
  ];

  async function doExport() {
    setExporting(true);
    try {
      const params = {
        ...(status ? { status: status as "SUCCESS" | "FAILED" | "REJECTED" | "NO_DATA" } : {}),
        ...(datasourceKey ? { datasource_key: datasourceKey } : {}),
        ...(fallback ? { fallback: fallback as "CHITCHAT" | "CLARIFY" | "none" } : {}),
      };
      const resp = await api.getBlob(qualityExportUrl(params));
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `问答质量-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a); // Safari 纪律:锚点入 DOM+延迟 revoke
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="问答质量"
        description="线上每一轮的四态占比、未覆盖分布与 prompt 版本切片——真实质量,不再靠猜。"
      />

      {/* 汇总卡(卡级错误不逐卡 role=alert,数值降 …/— 示错) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatCard label="总轮次" value={stat(String(ov?.total ?? 0))} />
        <StatCard label="成功率" value={stat(ov ? pct(ov.success / (ov.total || 1)) : "0.0%")} />
        <StatCard label="未覆盖率" value={stat(ov ? pct(ov.no_data_rate) : "0.0%")} />
        <StatCard label="自愈率" value={stat(ov ? pct(ov.selfheal_rate) : "0.0%")} />
        <StatCard
          label="AI 平均分"
          value={stat(ov ? (ov.avg_score != null ? ov.avg_score.toFixed(1) : "未评分") : "—")}
        />
      </div>

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
          <Button variant="outline" size="sm" onClick={() => void doExport()} disabled={exporting}>
            <Download className="mr-1 size-3.5" aria-hidden />
            {exporting ? "导出中…" : "导出 CSV"}
          </Button>
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
          <Select
            value={fallback || "all"}
            onValueChange={(v) => {
              setFallback(v === "all" ? "" : v);
              setPage(1);
            }}
          >
            <SelectTrigger size="sm" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部分类</SelectItem>
              <SelectItem value="CHITCHAT">闲聊</SelectItem>
              <SelectItem value="CLARIFY">澄清</SelectItem>
              <SelectItem value="none">无分类</SelectItem>
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
                  {r.fallback_type && (
                    <StatusBadge value={r.fallback_type} meta={FALLBACK_META} />
                  )}
                  {r.retry_count > 0 && (
                    <span className="text-xs text-muted-foreground">重试 {r.retry_count}</span>
                  )}
                </span>
              ),
            },
            {
              key: "rating",
              header: "反馈",
              render: (r) => (r.rating === "UP" ? "赞" : r.rating === "DOWN" ? "踩" : "—"),
            },
            {
              key: "score",
              header: "AI 评分",
              render: (r) =>
                r.score != null ? (
                  <Button
                    variant="link"
                    size="xs"
                    className={`h-auto p-0 font-mono ${
                      r.score < (ov?.low_score_threshold ?? 60)
                        ? "text-destructive"
                        : "text-foreground"
                    }`}
                    onClick={() => setScoreMessageId(r.id)}
                  >
                    {r.score}
                  </Button>
                ) : (
                  <span className="text-muted-foreground">未评分</span>
                ),
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
      <ScoreDetailDialog messageId={scoreMessageId} onClose={() => setScoreMessageId(null)} />
      <ContextDialog messageId={contextMessageId} onClose={() => setContextMessageId(null)} />
      <StageTraceDialog messageId={stageTraceId} onClose={() => setStageTraceId(null)} />
      <span className="hidden" aria-hidden>
        <BookOpen aria-hidden />
      </span>
    </div>
  );
}
