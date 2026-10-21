/* 报告 API 封装；接口类型由 ADR-005 管线生成。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type GenerateOut = components["schemas"]["GenerateOut"];
export type ReportItem = components["schemas"]["ReportItem"];
export type ReportPageOut = components["schemas"]["ReportPageOut"];
export type SectionOut = components["schemas"]["SectionOut"];
export type ReportDetailOut = components["schemas"]["ReportDetailOut"];
export type VersionMetaOut = components["schemas"]["VersionMetaOut"];
export type ProgressOut = components["schemas"]["ProgressOut"];
export type MutateOut = components["schemas"]["MutateOut"];
/** 报告章节的 bindings 是自由 JSONB；仓储写入结构变化时需同步此类型。 */
export type ReportBinding = {
  sql: string;
  status: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  fail_reason: string | null;
};

/** 发起生成(会话内与独立发起两入口同源;异步,返回即 GENERATING,进度走 getProgress 轮询) */
export function generateReport(body: {
  instruction: string;
  datasource_key?: string;
  session_id?: string | null;
  title?: string | null;
}): Promise<GenerateOut> {
  return api.post<GenerateOut>("/reports", body);
}

export function listReports(page: number, size = 20): Promise<ReportPageOut> {
  return api.get<ReportPageOut>(`/reports?page=${page}&size=${size}`);
}

export function getReport(id: string, version?: number): Promise<ReportDetailOut> {
  const qs = version ? `?version=${version}` : "";
  return api.get<ReportDetailOut>(`/reports/${id}${qs}`);
}

/** 生成进度(发起后轮询直至终态;轻量端点,只回 status/phase) */
export function getProgress(id: string): Promise<ProgressOut> {
  return api.get<ProgressOut>(`/reports/${id}/progress`);
}

export function listVersions(id: string): Promise<
  {
    id: string;
    version: number;
    meta: Record<string, unknown>;
    data_time_point: string;
    created_at: string;
  }[]
> {
  return api.get(`/reports/${id}/versions`);
}

/** 编辑保存(乐观锁:base_version 不匹配 409;仅标题/叙述可改) */
export function saveEdit(
  id: string,
  body: {
    base_version: number;
    sections: { section_id: string; title?: string | null; narrative_md?: string | null }[];
    title?: string | null;
  },
): Promise<MutateOut> {
  return api.put<MutateOut>(`/reports/${id}`, body);
}

/** 刷新数据(重执行绑定快照→新版本;防御拒绝章节如实 MISSING) */
export function refreshReport(id: string): Promise<MutateOut> {
  return api.post<MutateOut>(`/reports/${id}/refresh`, {});
}

export async function deleteReport(id: string): Promise<void> {
  await api.delete(`/reports/${id}`);
}

/** markdown 导出(所见即所导:渲染快照原样落表;文件名取 Content-Disposition) */
export async function exportReport(id: string, version?: number): Promise<void> {
  const qs = version ? `?version=${version}` : "";
  const resp = await api.getBlob(`/reports/${id}/export${qs}`);
  if (resp.headers.get("content-type")?.includes("application/json")) {
    const body: unknown = await resp.json();
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : "导出失败";
    throw new Error(detail);
  }
  const blob = await resp.blob();
  const disposition = resp.headers.get("content-disposition");
  const m = /filename="([^"]+)"/.exec(disposition ?? "");
  const filename = m?.[1] ?? `report-${id}.md`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/* 状态呈现单源(StatusBadge 消费);variant 用字面量联合保持结构兼容,lib 层不反向依赖组件 */
export const REPORT_STATUS_META: Record<
  string,
  { label: string; variant: "secondary" | "success" | "warning" | "destructive" | "outline" }
> = {
  GENERATING: { label: "生成中", variant: "secondary" },
  READY: { label: "就绪", variant: "success" },
  PARTIAL: { label: "部分完成", variant: "warning" },
  FAILED: { label: "失败", variant: "destructive" },
  DELETED: { label: "已删除", variant: "outline" },
};

export const REPORT_PHASE_LABEL: Record<string, string> = {
  PLANNING: "规划中",
  FETCHING: "取数中",
  WRITING: "撰写中",
  ASSEMBLING: "组装中",
  DONE: "已完成",
};
