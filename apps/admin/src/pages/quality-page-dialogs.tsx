/* 问答质量页的四个抽屉与趋势图:时间线、同会话轮次、节点轨迹、评分详情。 */

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, GitBranch } from "lucide-react";

import { EChart, themedTooltip } from "@kongming/api-client/echarts";
import { STAGE_LABELS } from "@kongming/ui/stream-card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { EmptyState } from "@kongming/ui/empty-state";
import { StatusBadge, type StatusMeta } from "@kongming/ui/status-badge";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { usageTimeline, type TimelineEvent } from "@kongming/api-client/usage-api";
import {
  qualityContext,
  qualityScoreDetail,
  qualityStageTrace,
  type QualityContextItemOut,
  type QualityTrendPoint,
} from "@kongming/api-client/quality-api";
import { formatDateTime } from "@kongming/api-client/datetime";
import { cellText, cssVar } from "@kongming/api-client/utils";
import {
  FALLBACK_META,
  IMPORTANCE_META,
  ROUND_STATUS_META,
  SUPPORT_META,
} from "./quality-page-meta";

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

/* 按天趋势(theme 经 key 作用于本组件:切主题整体重挂,option 重读 token 色板) */
export function QualityTrendChart({ points }: { points: QualityTrendPoint[] }) {
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
export function TimelineDialog({
  traceId,
  onClose,
}: {
  traceId: string | null;
  onClose: () => void;
}) {
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
        {(() => {
          if (timeline.isLoading) {
            return <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>;
          }
          if (timeline.isError) {
            return <ErrorAlert text="时间线加载失败" />;
          }
          if (events.length === 0) {
            return (
              <EmptyState icon={BookOpen}>
                <p className="font-medium text-foreground">暂无时间线事件</p>
                <p>该 trace_id 无 LLM/工具/SQL 事件记录。</p>
              </EmptyState>
            );
          }
          return (
            <ol className="space-y-2">
              {events.map((e, i) => (
                <li
                  key={`${e.ts}-${i}`}
                  className="flex items-center justify-between gap-2 text-sm"
                >
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
          );
        })()}
      </DialogContent>
    </Dialog>
  );
}

const TIMELINE_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  EXECUTED: { label: "已执行", variant: "secondary" },
  PASS: { label: "通过", variant: "secondary" },
  DONE: { label: "完成", variant: "secondary" },
  ERROR: { label: "失败", variant: "destructive" },
  REJECTED: { label: "已拒绝", variant: "destructive" },
  BLOCKED: { label: "拦截", variant: "destructive" },
};

export const pct = (v: number): string => `${(v * 100).toFixed(1)}%`;

/** 同会话轮次串联面板(spec「同会话轮次串联」;锚点轮必含,升序) */
export function ContextDialog({
  messageId,
  onClose,
}: {
  messageId: string | null;
  onClose: () => void;
}) {
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
        {(() => {
          if (ctx.isLoading) {
            return <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>;
          }
          if (ctx.isError) {
            return <ErrorAlert text="会话轮次加载失败" />;
          }
          return (
            <ol className="space-y-2">
              {items.map((it) => {
                const activeClass = it.id === messageId ? "border-primary bg-primary/5" : "";
                return (
                  <li key={it.id} className={`rounded-lg border p-2.5 text-sm ${activeClass}`}>
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
                );
              })}
            </ol>
          );
        })()}
      </DialogContent>
    </Dialog>
  );
}

/** 节点阶段轨迹抽屉(spec「节点阶段轨迹持久化」;null=同步轮如实为空) */
export function StageTraceDialog({
  messageId,
  onClose,
}: {
  messageId: string | null;
  onClose: () => void;
}) {
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
        {(() => {
          if (trace.isLoading) {
            return <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>;
          }
          if (trace.isError) {
            return <ErrorAlert text="轨迹加载失败" />;
          }
          if (nodes.length === 0) {
            return (
              <EmptyState icon={GitBranch}>
                <p className="font-medium text-foreground">无轨迹</p>
                <p>同步问答轮不产生节点阶段轨迹。</p>
              </EmptyState>
            );
          }
          return (
            <>
              {truncated && (
                <p className="text-xs text-warning">条目超上限,仅保留最近记录(已截断)。</p>
              )}
              <ol className="space-y-1.5">
                {nodes.map((e, i) => {
                  const node = cellText((e as Record<string, unknown>).node);
                  const status = cellText((e as Record<string, unknown>).status);
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
          );
        })()}
      </DialogContent>
    </Dialog>
  );
}

/** 评分详情抽屉(维度分+主张分解;404=未评分或越权,同形提示) */
export function ScoreDetailDialog({
  messageId,
  onClose,
}: {
  messageId: string | null;
  onClose: () => void;
}) {
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
        {(() => {
          if (detail.isLoading) {
            return <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>;
          }
          if (detail.isError) {
            return <ErrorAlert text="评分详情加载失败(可能未评分或无权查看)" />;
          }
          if (!detail.data) return null;
          return (
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
          );
        })()}
      </DialogContent>
    </Dialog>
  );
}
