/* task 域 API 封装(task-kernel);类型契约由生成管线提供(ADR-005,禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type TaskItem = components["schemas"]["TaskItem"];
export type TaskPageOut = components["schemas"]["TaskPageOut"];
export type TaskDetailOut = components["schemas"]["TaskDetailOut"];
export type StepOut = components["schemas"]["StepOut"];
export type TaskMutateOut = components["schemas"]["TaskMutateOut"];
export type CollabRunOut = components["schemas"]["CollabRunOut"];
export type CollabRunsOut = components["schemas"]["CollabRunsOut"];
export type CollabWorkerOut = components["schemas"]["CollabWorkerOut"];
export type CollabTurnOut = components["schemas"]["CollabTurnOut"];

/** 创建任务(异步;202 即返回 PLANNING,详情轮询状态) */
export function createTask(body: {
  goal: string;
  datasource_key?: string;
  session_id?: string | null;
  budget?: Record<string, number> | null;
}): Promise<components["schemas"]["TaskCreateOut"]> {
  return api.post("/tasks", body);
}

export function listTasks(page: number, size = 20, status?: string): Promise<TaskPageOut> {
  const s = status ? `&status=${encodeURIComponent(status)}` : "";
  return api.get<TaskPageOut>(`/tasks?page=${page}&size=${size}${s}`);
}

export function getTask(id: string): Promise<TaskDetailOut> {
  return api.get<TaskDetailOut>(`/tasks/${id}`);
}

/* 协作观测面:任务维度协作运行清单(worker 账本+裁决) */
export function listCollabRuns(id: string): Promise<CollabRunsOut> {
  return api.get<CollabRunsOut>(`/tasks/${id}/collab-runs`);
}

/** 步骤声明协作拓扑(PENDING 步骤;归一+部署钳制在服务端) */
export function setCollabTopology(
  id: string,
  stepId: string,
  body: {
    max_workers?: number | null;
    critic?: boolean;
    dispatch?: string | null;
    bot_key?: string | null;
  },
): Promise<Record<string, unknown>> {
  return api.put(`/tasks/${id}/steps/${stepId}/collab-topology`, body);
}

export function pauseTask(id: string, version?: number): Promise<TaskMutateOut> {
  const qs = version !== undefined ? `?version=${version}` : "";
  return api.post<TaskMutateOut>(`/tasks/${id}/pause${qs}`, {});
}

export function resumeTask(id: string): Promise<TaskMutateOut> {
  return api.post<TaskMutateOut>(`/tasks/${id}/resume`, {});
}

export function cancelTask(id: string): Promise<TaskMutateOut> {
  return api.post<TaskMutateOut>(`/tasks/${id}/cancel`, {});
}

export function approveStep(id: string, stepId: string, approve: boolean): Promise<TaskMutateOut> {
  return api.post<TaskMutateOut>(`/tasks/${id}/steps/${stepId}/approve`, { approve });
}

export type GraphConfirmOut = components["schemas"]["GraphConfirmOut"];

/** 图内确认处置(graph-checkpoint):批准=动作恢复执行,拒绝=动作跳过。 */
export function decideGraphConfirm(
  id: string,
  confirmId: string,
  approve: boolean,
): Promise<TaskMutateOut> {
  return api.post<TaskMutateOut>(`/tasks/${id}/confirms/${confirmId}`, { approve });
}

/* 状态呈现单源(StatusBadge 消费;字面量 variant 与 Badge 联合类型结构兼容) */
export const TASK_STATUS_META: Record<
  string,
  { label: string; variant: "secondary" | "success" | "warning" | "destructive" | "outline" }
> = {
  PLANNING: { label: "规划中", variant: "secondary" },
  RUNNING: { label: "执行中", variant: "success" },
  AWAITING_APPROVAL: { label: "待审批", variant: "warning" },
  PAUSED: { label: "已暂停", variant: "warning" },
  INTERRUPTED: { label: "已中断", variant: "warning" },
  DONE: { label: "已完成", variant: "success" },
  FAILED: { label: "失败", variant: "destructive" },
  CANCELLED: { label: "已取消", variant: "outline" },
};

/* 协作账本状态呈现:运行态与 worker 态共用一族(PARTIAL 为运行独有) */
export const COLLAB_STATUS_META: Record<
  string,
  { label: string; variant: "secondary" | "success" | "warning" | "destructive" | "outline" }
> = {
  RUNNING: { label: "进行中", variant: "success" },
  DONE: { label: "完成", variant: "success" },
  PARTIAL: { label: "部分完成", variant: "warning" },
  FAILED: { label: "失败", variant: "destructive" },
  CANCELLED: { label: "已取消", variant: "outline" },
  PENDING: { label: "待派发", variant: "secondary" },
  SKIPPED: { label: "已跳过", variant: "outline" },
};
