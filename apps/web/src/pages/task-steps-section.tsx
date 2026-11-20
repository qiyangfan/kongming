/* 任务步骤账本:步骤状态、审批门、图内确认卡与断点摘要。 */

import { MarkdownText } from "@/components/markdown-text";
import { StatusBadge } from "@/components/status-badge";
import type { GraphConfirmOut, TaskDetailOut } from "@/lib/task-api";
import { TASK_STATUS_META } from "@/lib/task-api";

import { ConfirmOperationButton, OperationButton } from "./task-operation-buttons";

type TaskStep = TaskDetailOut["steps"][number];

/** 图内确认卡(graph-checkpoint):任务循环内高危动作挂图审批;
 * 处置纪律与任务审批门同源(归属人-only/过期/幂等),呈现沿用确认卡形态。 */
function GraphConfirmCard({
  confirm,
  disabled,
  onDecide,
}: {
  confirm: GraphConfirmOut;
  disabled?: boolean | undefined;
  onDecide: (approve: boolean) => void;
}) {
  if (confirm.status !== "pending") {
    const label = CONFIRM_LABELS[confirm.status] ?? "已过期";
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

/** 待审批步骤的批准/拒绝按钮组。 */
function ApprovalButtons({
  step,
  pending,
  onApprove,
}: {
  step: TaskStep;
  pending: boolean;
  onApprove: (stepId: string, approve: boolean) => void;
}) {
  return (
    <div className="flex gap-2 pt-1">
      <OperationButton label="批准" onClick={() => onApprove(step.id, true)} disabled={pending} />
      <ConfirmOperationButton
        label="拒绝"
        title="拒绝这个步骤?"
        description="该步骤将置为 SKIPPED(不再执行),任务按其余步骤继续;任务将如实以 FAILED 收口并说明未完成原因。"
        confirmLabel="拒绝执行"
        onClick={() => onApprove(step.id, false)}
        disabled={pending}
      />
    </div>
  );
}

/** 步骤的断点摘要与恢复方式说明。 */
function StepCheckpoint({ step }: { step: TaskStep }) {
  const cp = step.checkpoint;
  return (
    <>
      {cp && typeof cp.summary === "string" && cp.summary !== "" && (
        <MarkdownText text={cp.summary} />
      )}
      {cp && cp.resume_origin === "snapshot" && (
        <p className="text-xs text-muted-foreground">恢复方式:快照续跑(非首次执行)。</p>
      )}
    </>
  );
}

interface TaskStepsSectionProps {
  steps: TaskStep[];
  graphConfirms: GraphConfirmOut[] | undefined;
  approvePending: boolean;
  confirmPending: boolean;
  onApprove: (stepId: string, approve: boolean) => void;
  onConfirm: (confirmId: string, approve: boolean) => void;
}

/** 步骤列表:每步的标题/状态、审批门、图内确认与断点。 */
const CONFIRM_LABELS: Record<string, string> = { approved: "已批准", denied: "已拒绝" };

export function TaskStepsSection(props: TaskStepsSectionProps) {
  return (
    <section className="space-y-2" aria-label="步骤">
      <h3 className="text-sm font-semibold text-foreground">步骤</h3>
      {props.steps.map((s) => (
        <div key={s.id} className="space-y-1 rounded-lg border border-border p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium text-foreground">
              {s.ord + 1}. {s.goal}
            </span>
            <StatusBadge meta={TASK_STATUS_META} value={s.status} fallbackVariant="outline" />
          </div>
          {s.status === "AWAITING_APPROVAL" && (
            <ApprovalButtons step={s} pending={props.approvePending} onApprove={props.onApprove} />
          )}
          {s.fail_reason && <p className="text-xs text-muted-foreground">{s.fail_reason}</p>}
          {(props.graphConfirms ?? [])
            .filter((c) => c.step_id === s.id)
            .map((c) => (
              <GraphConfirmCard
                key={c.confirm_id}
                confirm={c}
                disabled={props.confirmPending}
                onDecide={(ok) => props.onConfirm(c.confirm_id, ok)}
              />
            ))}
          <StepCheckpoint step={s} />
        </div>
      ))}
    </section>
  );
}
