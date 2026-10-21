import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, GitBranch, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { DataView, type DataViewColumn } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { MarkdownText } from "@/components/markdown-text";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  COLLAB_STATUS_META,
  decideGraphConfirm,
  getTask,
  listCollabRuns,
  TASK_STATUS_META,
} from "@/lib/task-api";
import type { CollabRunOut, CollabTurnOut, CollabWorkerOut, GraphConfirmOut } from "@/lib/task-api";
import { useTenant } from "@/lib/tenant-store";

// 活动期轮询节奏;终态即停
const TASK_ACTIVE_POLL_MS = 2000;
const COLLAB_RUNS_POLL_MS = 4000;
// 协作运行在途只可能发生在 PLANNING/RUNNING/PAUSED:审批挂起/中断期无执行者;
// 暂停不杀在途编排循环,PAUSED 下仍可能有收尾中的运行
const RUNS_ACTIVE_STATUSES = ["PLANNING", "RUNNING", "PAUSED"];
// 裁决值中文映射(未知值原样透出)
/** 圆桌轮次行类型呈现:发言/查证/弃权/主席判定。 */
const ROUND_TURN_KIND_LABELS: Record<string, string> = {
  speech: "发言",
  verification: "查证",
  abstain: "弃权",
  verdict: "判定",
};

const COLLAB_VERDICT_LABELS: Record<string, string> = {
  PASS: "通过",
  ERROR: "失败",
  superseded: "已被重跑接管",
};

export default function TaskDetailPage() {
  const { taskId = "" } = useParams();
  const qc = useQueryClient();
  const tenant = useTenant((s) => s.current);
  const [actionError, setActionError] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["task", tenant, taskId],
    queryFn: () => getTask(taskId),
    refetchInterval: (q) =>
      ["PLANNING", "RUNNING"].includes(q.state.data?.task.status ?? "")
        ? TASK_ACTIVE_POLL_MS
        : false,
  });

  const active = ["PLANNING", "RUNNING", "AWAITING_APPROVAL", "PAUSED", "INTERRUPTED"].includes(
    detail.data?.task.status ?? "",
  );
  const runs = useQuery({
    queryKey: ["task-collab-runs", tenant, taskId],
    queryFn: () => listCollabRuns(taskId),
    // 仅协作可能在途的任务态轮询刷新;终态/审批挂起/中断一次性读取
    refetchInterval: RUNS_ACTIVE_STATUSES.includes(detail.data?.task.status ?? "")
      ? COLLAB_RUNS_POLL_MS
      : false,
    retry: false,
  });

  function invalidate(): void {
    void qc.invalidateQueries({ queryKey: ["task", tenant, taskId] });
    void qc.invalidateQueries({ queryKey: ["tasks", tenant] });
  }

  const act = useMutation({
    mutationFn: async (kind: "pause" | "resume" | "cancel") => {
      const { pauseTask, resumeTask, cancelTask } = await import("@/lib/task-api");
      if (kind === "pause") return pauseTask(taskId);
      if (kind === "resume") return resumeTask(taskId);
      return cancelTask(taskId);
    },
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "操作未成"),
  });

  const approve = useMutation({
    mutationFn: async (input: { stepId: string; approve: boolean }) => {
      const { approveStep } = await import("@/lib/task-api");
      return approveStep(taskId, input.stepId, input.approve);
    },
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "审批未成"),
  });

  // 图内确认处置(add-graph-checkpoint):批准=动作恢复执行,拒绝=动作跳过;
  // 恢复是编排循环唯一的再启动口,处置后控制面自动重派
  const confirm = useMutation({
    mutationFn: async (input: { confirmId: string; approve: boolean }) =>
      decideGraphConfirm(taskId, input.confirmId, input.approve),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "确认处置未成"),
  });

  if (detail.isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-3 px-4 py-6">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-6">
        <ErrorAlert text="任务不存在或不可见。" />
        <ButtonLink />
      </div>
    );
  }

  const d = detail.data;

  return (
    <div className="mx-auto max-w-4xl space-y-5 px-4 py-6">
      <ButtonLink />
      <PageHeader
        title={d.task.goal}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge meta={TASK_STATUS_META} value={d.task.status} />
            <span className="text-xs text-muted-foreground">
              创建于 {new Date(d.task.created_at).toLocaleString()}
            </span>
          </span>
        }
        actions={
          active && (
            <>
              <OperationButton
                label="暂停"
                onClick={() => act.mutate("pause")}
                disabled={act.isPending || d.task.status !== "RUNNING"}
              />
              <OperationButton
                label="恢复"
                onClick={() => act.mutate("resume")}
                disabled={
                  act.isPending ||
                  !["PAUSED", "INTERRUPTED", "AWAITING_APPROVAL"].includes(d.task.status)
                }
              />
              <ConfirmOperationButton
                label="取消"
                title="取消这个任务?"
                description={`「${d.task.goal}」将进入 CANCELLED 终态,未完成步骤一并关闭;此操作不可撤销。`}
                confirmLabel="取消任务"
                onClick={() => act.mutate("cancel")}
                disabled={act.isPending}
              />
            </>
          )
        }
      />

      {actionError && <ErrorAlert text={actionError} />}
      {d.task.fail_reason && (
        <p role="status" className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
          {d.task.fail_reason}
        </p>
      )}

      {/* 步骤账本 */}
      <section className="space-y-2" aria-label="步骤">
        <h3 className="text-sm font-semibold text-foreground">步骤</h3>
        {d.steps.map((s) => (
          <div key={s.id} className="space-y-1 rounded-lg border border-border p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-foreground">
                {s.ord + 1}. {s.goal}
              </span>
              <StatusBadge meta={TASK_STATUS_META} value={s.status} fallbackVariant="outline" />
            </div>
            {s.status === "AWAITING_APPROVAL" && (
              <div className="flex gap-2 pt-1">
                <OperationButton
                  label="批准"
                  onClick={() => approve.mutate({ stepId: s.id, approve: true })}
                  disabled={approve.isPending}
                />
                <ConfirmOperationButton
                  label="拒绝"
                  title="拒绝这个步骤?"
                  description="该步骤将置为 SKIPPED(不再执行),任务按其余步骤继续;任务将如实以 FAILED 收口并说明未完成原因。"
                  confirmLabel="拒绝执行"
                  onClick={() => approve.mutate({ stepId: s.id, approve: false })}
                  disabled={approve.isPending}
                />
              </div>
            )}
            {s.fail_reason && <p className="text-xs text-muted-foreground">{s.fail_reason}</p>}
            {(d.graph_confirms ?? [])
              .filter((c) => c.step_id === s.id)
              .map((c) => (
                <GraphConfirmCard
                  key={c.confirm_id}
                  confirm={c}
                  disabled={confirm.isPending}
                  onDecide={(ok) => confirm.mutate({ confirmId: c.confirm_id, approve: ok })}
                />
              ))}
            {s.checkpoint &&
              typeof s.checkpoint.summary === "string" &&
              s.checkpoint.summary !== "" && <MarkdownText text={s.checkpoint.summary} />}
            {s.checkpoint && s.checkpoint.resume_origin === "snapshot" && (
              <p className="text-xs text-muted-foreground">恢复方式:快照续跑(非首次执行)。</p>
            )}
          </div>
        ))}
      </section>

      {/* 协作视图:worker 账本 + 裁决;加载失败如实呈现,有账才渲染 */}
      {runs.isError && <ErrorAlert text="协作账本加载失败,可刷新重试。" />}
      {runs.data && runs.data.items.length > 0 && (
        <section className="space-y-2" aria-label="协作">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
            <GitBranch className="size-4" aria-hidden /> 协作
          </h3>
          {runs.data.items.map((run) => {
            const finEvent = d.events.find((e) => e.kind === "collab_finished" && e.run === run.id);
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
      )}

      {/* 事件时间线 */}
      <section className="space-y-1" aria-label="事件">
        <h3 className="text-sm font-semibold text-foreground">事件</h3>
        {d.events.length === 0 ? (
          <p className="text-xs text-muted-foreground">暂无事件。</p>
        ) : (
          <ol className="space-y-0.5">
            {/* key 取正序索引(追加式事件流稳定),反转为最新在前 */}
            {[...d.events]
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
    </div>
  );
}

function ButtonLink() {
  return (
    <Link to="/tasks" className="flex items-center gap-1 text-sm text-muted-foreground">
      <ArrowLeft className="size-4" aria-hidden /> 任务列表
    </Link>
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
  const workerColumns: DataViewColumn<CollabWorkerOut>[] = [
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
  const topo = run.topology ?? {};
  const workers = run.workers ?? [];
  const turns = run.turns ?? [];
  const hasVerification = workers.some((w) => w.conclusion?.verification);
  const isRoundtable = topo.kind === "roundtable";
  const rosterSize = Array.isArray(topo.workers) ? topo.workers.length : 0;
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge meta={COLLAB_STATUS_META} value={run.status} />
        {run.verdict && (
          <span className="text-xs text-muted-foreground">
            裁决 {COLLAB_VERDICT_LABELS[run.verdict] ?? run.verdict}
          </span>
        )}
        {isRoundtable ? (
          <span className="text-xs text-muted-foreground">
            圆桌·{Array.isArray(topo.participants) ? topo.participants.length : "—"} 参与者·≤
            {String(topo.max_rounds ?? "—")} 轮
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            {String(topo.max_workers ?? "—")} worker
            {rosterSize > 0 ? `·名册 ${rosterSize}` : ""}·{String(topo.dispatch ?? "serial")}
            {topo.critic ? "·critic" : ""}
          </span>
        )}
      </div>
      {/* 分歧/部分完成如实披露(不静默调和的呈现面);两态并存时各自成行,
          事件滞后时缺数不臆造 0 */}
      {(run.status === "PARTIAL" || pendingCount !== null) && (
        <p className="flex items-start gap-1.5 text-xs text-warning">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          存在未派发子目标{pendingCount === null ? "" : `(${pendingCount})`}
          ,本次运行为部分完成。
        </p>
      )}
      {hasVerification && (
        <p className="flex items-start gap-1.5 text-xs text-warning">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          存在 worker 结论分歧,已按核实结果或标注呈现。
        </p>
      )}
      {isRoundtable && dissent === true && (
        <p className="flex items-start gap-1.5 text-xs text-warning">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          圆桌未达成共识,分歧各方立场如实保留。
        </p>
      )}
      <DataView
        columns={workerColumns}
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

/** 按轮次和发言顺序生成圆桌记录列；参与者优先使用行内标签。 */
function buildTurnColumns(): DataViewColumn<CollabTurnOut>[] {
  return [
    { key: "round_no", header: "轮次", cardPriority: 1 },
    {
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
    },
    {
      key: "kind",
      header: "类型",
      cardPriority: 2,
      render: (t) => ROUND_TURN_KIND_LABELS[t.kind] ?? t.kind,
    },
    {
      key: "stance",
      header: "立场/判定",
      render: (t) => {
        if (t.kind === "verdict") return t.content?.consensus === true ? "共识" : "未共识";
        if (t.kind === "abstain") return "弃权";
        const stance = t.content && typeof t.content.stance === "string" ? t.content.stance : "—";
        return <span className="text-xs">{stance}</span>;
      },
    },
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

/** 图内确认卡(graph-checkpoint):任务循环内高危动作挂图审批;
 * 处置纪律与任务审批门同源(归属人-only/过期/幂等),呈现沿用确认卡形态。 */
function GraphConfirmCard({
  confirm,
  disabled,
  onDecide,
}: {
  confirm: GraphConfirmOut;
  disabled?: boolean;
  onDecide: (approve: boolean) => void;
}) {
  if (confirm.status !== "pending") {
    const label =
      confirm.status === "approved" ? "已批准" : confirm.status === "denied" ? "已拒绝" : "已过期";
    return (
      <p className="text-xs text-muted-foreground">
        图内确认({confirm.tool_name}):{label}。
      </p>
    );
  }
  return (
    <div className="space-y-1 rounded-md border border-warning/40 bg-warning/10 p-2">
      <p className="text-xs text-foreground">
        待人工确认的动作:
        <span className="font-medium">{confirm.tool_name}</span>
        {confirm.summary ? ` — ${confirm.summary}` : ""}
      </p>
      <div className="flex gap-2">
        <OperationButton label="批准执行" onClick={() => onDecide(true)} disabled={disabled} />
        <ConfirmOperationButton
          label="拒绝"
          title="拒绝这个动作?"
          description="该动作将不执行(作为拒绝观察喂回执行循环),任务按编排如实收口并说明未完成原因。"
          confirmLabel="拒绝动作"
          onClick={() => onDecide(false)}
          disabled={disabled}
        />
      </div>
    </div>
  );
}

function OperationButton(props: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={props.onClick} disabled={props.disabled}>
      {props.label}
    </Button>
  );
}

/** 破坏性操作按钮(宪法 Article 2:不可逆终态迁移必须 AlertDialog 确认)。 */
function ConfirmOperationButton(props: {
  label: string;
  title: string;
  description: string;
  confirmLabel: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="sm" className="text-destructive" disabled={props.disabled}>
          {props.label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{props.title}</AlertDialogTitle>
          <AlertDialogDescription>{props.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>再想想</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={props.onClick}>
            {props.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
