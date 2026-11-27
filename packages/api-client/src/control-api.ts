/* 控制面状态与本地命令端点(管理台)。类型取生成契约(ADR-005)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type ControlStatusOut = components["schemas"]["ControlStatusOut"];
export type ReceiptOut = components["schemas"]["ReceiptOut"];
export type LocalCommandIn = components["schemas"]["LocalCommandIn"];
export type LocalCommandOut = components["schemas"]["LocalCommandOut"];
export type SharedSnapshotOut = components["schemas"]["SharedSnapshotOut"];

export const controlApi = {
  status: () => api.get<ControlStatusOut>("/system/control"),
  snapshot: () => api.get<SharedSnapshotOut>("/system/control/snapshot"),
  receipts: (limit = 50) =>
    api.get<ReceiptOut>(`/system/commands/receipts?limit=${limit}`),
  runLocal: (body: LocalCommandIn) =>
    api.post<LocalCommandOut>("/system/commands", body),
};
