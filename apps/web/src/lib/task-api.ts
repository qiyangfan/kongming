/* task 域 API 封装(090 task-kernel)。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type TaskItem = components["schemas"]["TaskItem"];
export type TaskPageOut = components["schemas"]["TaskPageOut"];
export type TaskDetailOut = components["schemas"]["TaskDetailOut"];
export type StepOut = components["schemas"]["StepOut"];
export type TaskMutateOut = components["schemas"]["TaskMutateOut"];

/** 创建任务(异步;202 即返回 PLANNING,详情轮询状态) */
export function createTask(body: {
  goal: string;
  datasource_key?: string;
  session_id?: string | null;
  budget?: Record<string, number> | null;
}): Promise<components["schemas"]["TaskCreateOut"]> {
  return api.post("/tasks", body);
}

export function listTasks(page: number, size = 20): Promise<TaskPageOut> {
  return api.get<TaskPageOut>(`/tasks?page=${page}&size=${size}`);
}

export function getTask(id: string): Promise<TaskDetailOut> {
  return api.get<TaskDetailOut>(`/tasks/${id}`);
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
