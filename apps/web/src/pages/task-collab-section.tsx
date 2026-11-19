/* 任务协作视图:一次运行的裁决态、分歧/部分完成披露与 worker 账本;圆桌运行另附轮次转写。 */

import { GitBranch, TriangleAlert } from "lucide-react";

import { DataView, type DataViewColumn } from "@/components/data-view";
import { StatusBadge } from "@/components/status-badge";
import type { CollabRunOut, CollabTurnOut, CollabWorkerOut, TaskDetailOut } from "@/lib/task-api";
import { COLLAB_STATUS_META } from "@/lib/task-api";

// 裁决值中文映射(未知值原样透出)
const COLLAB_VERDICT_LABELS: Record<string, string> = {
  PASS: "通过",
  ERROR: "失败",
  superseded: "已被重跑接管",
};

/** 圆桌轮次行类型呈现:发言/查证/弃权/主席判定。 */
const ROUND_TURN_KIND_LABELS: Record<string, string> = {
  speech: "发言",
  verification: "查证",
  abstain: "弃权",
  verdict: "判定",
};

const WORKER_COLUMNS: DataViewColumn<CollabWorkerOut>[] = [
  {
    key: "subgoal",
    header: "子目标",
    cardPriority: 1,
    render: (w) => (
      <span className="font-medium text-foreground">
        #{w.worker_no} {w.subgoal}
      </span>
    ),
  },
  {
    key: "status",
    header: "状态",
    cardPriority: 2,
    render: (w) => (
      <StatusBadge meta={COLLAB_STATUS_META} value={w.status} fallbackVariant="outline" />
    ),
  },
  {
    key: "conclusion",
    header: "结论摘要",
    render: (w) => {
      const summary =
        w.conclusion && typeof w.conclusion.summary === "string" ? w.conclusion.summary : "—";
      return <span className="line-clamp-2 text-xs text-muted-foreground">{summary}</span>;
    },
  },
  {
    key: "usage",
    header: "token",
    render: (w) =>
      w.usage && typeof w.usage.total_tokens === "number" ? w.usage.total_tokens : "—",
  },
  {
    key: "bot_key",
    header: "绑定",
    render: (w) => (w.bot_key ? <span className="font-mono text-xs">{w.bot_key}</span> : "—"),
  },
  {
    key: "trace_id",
    header: "trace",
    render: (w) => <span className="font-mono text-xs">{w.trace_id ?? "—"}</span>,
  },
];

/** 圆桌参与者列:主席固定为 0 号;标签优先,退回角色名。 */
function turnWorkerColumn(): DataViewColumn<CollabTurnOut> {
  return {
    key: "worker_no",
    header: "参与者",
    cardPriority: 2,
    render: (t) => {
      if (t.worker_no === 0) return <span className="font-medium text-foreground">主席</span>;
      const label =
        t.content && typeof t.content.label === "string" && t.content.label !== ""
          ? t.content.label
          : null;
      const role =
        t.content && typeof t.content.role === "string" && t.content.role !== ""
          ? t.content.role
          : null;
      // 兜底 text 置空而非 #N:外层已带序号前缀,重复拼 #N 会渲染成「#1 #1」
      const text = label ?? role ?? "";
      return (
        <span className="font-medium text-foreground">
          #{t.worker_no}
          {text ? ` ${text}` : ""}
        </span>
      );
    },
  };
}

/** 圆桌轮次的立场/判定列。 */
function turnStanceColumn(): DataViewColumn<CollabTurnOut> {
  return {
    key: "stance",
    header: "立场/判定",
    render: (t) => {
      if (t.kind === "verdict") return t.content?.consensus === true ? "共识" : "未共识";
      if (t.kind === "abstain") return "弃权";
      const stance = t.content && typeof t.content.stance === "string" ? t.content.stance : "—";
      return <span className="text-xs">{stance}</span>;
    },
  };
}

/** 按轮次和发言顺序生成圆桌记录列；参与者优先使用行内标签。 */
function buildTurnColumns(): DataViewColumn<CollabTurnOut>[] {
  return [
    { key: "round_no", header: "轮次", cardPriority: 1 },
    turnWorkerColumn(),
    {
      key: "kind",
      header: "类型",
      cardPriority: 2,
      render: (t) => ROUND_TURN_KIND_LABELS[t.kind] ?? t.kind,
    },
    turnStanceColumn(),
    {
      key: "summary",
      header: "摘要",
      render: (t) => {
        const summary = t.content && typeof t.content.summary === "string" ? t.content.summary : "";
        return <span className="line-clamp-2 text-xs text-muted-foreground">{summary || "—"}</span>;
      },
    },
    {
      key: "usage",
      header: "token",
      render: (t) =>
        t.usage && typeof t.usage.total_tokens === "number" ? t.usage.total_tokens : "—",
    },
    {
      key: "trace_id",
      header: "trace",
      render: (t) => <span className="font-mono text-xs">{t.trace_id || "—"}</span>,
    },
  ];
}

/** 运行头部的拓扑说明:圆桌给参与者与轮数,否则给 worker 上限/名册/派发方式。 */
function TopologyLine({ run }: { run: CollabRunOut }) {
  const topo = run.topology ?? {};
  const rosterSize = Array.isArray(topo.workers) ? topo.workers.length : 0;
  if (topo.kind === "roundtable") {
    return (
      <span className="text-xs text-muted-foreground">
        圆桌·{Array.isArray(topo.participants) ? topo.participants.length : "—"} 参与者·≤
        {String(topo.max_rounds ?? "—")} 轮
      </span>
    );
  }
  return (
    <span className="text-xs text-muted-foreground">
      {String(topo.max_workers ?? "—")} worker
      {rosterSize > 0 ? `·名册 ${rosterSize}` : ""}·{String(topo.dispatch ?? "serial")}
      {topo.critic ? "·critic" : ""}
    </span>
  );
}

/** 单条分歧/部分完成披露行。 */
function WarningLine({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-warning">
      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      {children}
    </p>
  );
}

/** 分歧/部分完成如实披露(不静默调和的呈现面);事件滞后时缺数不臆造 0。 */
function RunWarnings({
  run,
  pendingCount,
  dissent,
  hasVerification,
  isRoundtable,
}: {
  run: CollabRunOut;
  pendingCount: number | null;
  dissent: boolean | null;
  hasVerification: boolean;
  isRoundtable: boolean;
}) {
  return (
    <>
      {(run.status === "PARTIAL" || pendingCount !== null) && (
        <WarningLine>
          存在未派发子目标{pendingCount === null ? "" : `(${pendingCount})`}
          ,本次运行为部分完成。
        </WarningLine>
      )}
      {hasVerification && <WarningLine>存在 worker 结论分歧,已按核实结果或标注呈现。</WarningLine>}
      {isRoundtable && dissent === true && (
        <WarningLine>圆桌未达成共识,分歧各方立场如实保留。</WarningLine>
      )}
    </>
  );
}

/** 一次协作运行卡片:裁决态 + 分歧/部分完成披露 + worker 账本(DataView 双形态)。 */
function CollabRunCard({
  run,
  pendingCount,
  dissent,
}: {
  run: CollabRunOut;
  pendingCount: number | null;
  dissent: boolean | null;
}) {
  const topo = run.topology ?? {};
  const workers = run.workers ?? [];
  const turns = run.turns ?? [];
  const hasVerification = workers.some((w) => w.conclusion?.verification);
  const isRoundtable = topo.kind === "roundtable";
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge meta={COLLAB_STATUS_META} value={run.status} />
        {run.verdict && (
          <span className="text-xs text-muted-foreground">
            裁决 {COLLAB_VERDICT_LABELS[run.verdict] ?? run.verdict}
          </span>
        )}
        <TopologyLine run={run} />
      </div>
      <RunWarnings
        run={run}
        pendingCount={pendingCount}
        dissent={dissent}
        hasVerification={hasVerification}
        isRoundtable={isRoundtable}
      />
      <DataView
        columns={WORKER_COLUMNS}
        rows={workers}
        rowKey={(w) => String(w.worker_no)}
        isEmpty={workers.length === 0}
        emptyText="无 worker 账本。"
      />
      {isRoundtable && turns.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">轮次转写(按序回放)</p>
          <DataView
            columns={buildTurnColumns()}
            rows={turns}
            rowKey={(t) => `${t.round_no}-${t.worker_no}`}
            isEmpty={false}
            emptyText=""
          />
        </div>
      )}
    </div>
  );
}

interface TaskCollabSectionProps {
  items: CollabRunOut[];
  events: TaskDetailOut["events"];
}

/** 协作区:worker 账本 + 裁决;有账才渲染。 */
export function TaskCollabSection({ items, events }: TaskCollabSectionProps) {
  return (
    <section className="space-y-2" aria-label="协作">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
        <GitBranch className="size-4" aria-hidden /> 协作
      </h3>
      {items.map((run) => {
        const finEvent = events.find((e) => e.kind === "collab_finished" && e.run === run.id);
        return (
          <CollabRunCard
            key={run.id}
            run={run}
            // 详情轮询暂停期间事件可能滞后:无事件时不下发臆造的 0
            pendingCount={typeof finEvent?.pending === "number" ? finEvent.pending : null}
            dissent={typeof finEvent?.dissent === "boolean" ? finEvent.dissent : null}
          />
        );
      })}
    </section>
  );
}

/** 事件时间线:最新在前,未知字段按原值透出。 */
export function TaskEventsSection({ events }: { events: TaskDetailOut["events"] }) {
  return (
    <section className="space-y-1" aria-label="事件">
      <h3 className="text-sm font-semibold text-foreground">事件</h3>
      {events.length === 0 ? (
        <p className="text-xs text-muted-foreground">暂无事件。</p>
      ) : (
        <ol className="space-y-0.5">
          {/* key 取正序索引(追加式事件流稳定),反转为最新在前 */}
          {[...events]
            .map((e, i) => (
              <li
                key={`${String(e.at)}-${String(e.kind)}-${i}`}
                className="text-xs text-muted-foreground"
              >
                {typeof e.at === "string" ? new Date(e.at).toLocaleString() : ""} —{" "}
                {String(e.kind ?? "")}
                {e.reason ? `(${String(e.reason)})` : ""}
              </li>
            ))
            .reverse()}
        </ol>
      )}
    </section>
  );
}
