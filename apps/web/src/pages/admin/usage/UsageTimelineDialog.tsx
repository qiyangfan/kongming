/* 调用链路对话框:按 trace 展示事件时间线。 */

import { Activity } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/empty-state";
import { ErrorAlert } from "@/components/error-alert";
import type { TimelineEvent } from "@/lib/usage-api";
import { formatDateTime, formatDuration } from "@/lib/datetime";

import { fmtTokens, isErrorStatus, KIND_ICON, kindLabel, kindStatusLabel } from "./usage-format";

function TimelineItem({ event }: { event: TimelineEvent }) {
  const Icon = KIND_ICON[event.kind] ?? Activity;
  const isErr = isErrorStatus(event.status);
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
      <div className="mt-0.5 text-xs text-muted-foreground">{kindLabel(event.kind)}</div>
    </li>
  );
}

export function UsageTimelineDialog({
  traceId,
  timeline,
  onClose,
}: {
  traceId: string | null;
  timeline: { isLoading: boolean; isError: boolean; data?: { events: TimelineEvent[] } };
  onClose: () => void;
}) {
  return (
    <Dialog open={traceId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Activity className="size-4" />
            调用链路
          </DialogTitle>
          <DialogDescription
            className="max-w-full truncate font-mono text-xs"
            title={traceId ?? undefined}
          >
            {traceId}
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
  );
}
