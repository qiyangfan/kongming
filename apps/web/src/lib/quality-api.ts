/* 轮次质量观测域 API 封装。类型契约由 ADR-005 管线生成(禁手写);两端点同挂 system:chat-quality:view。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type QualitySummaryOut = components["schemas"]["QualitySummaryOut"];
export type QualityPageOut = components["schemas"]["QualityPageOut"];
export type QualityRoundItemOut = components["schemas"]["QualityRoundItemOut"];
export type QualityTrendPoint = QualitySummaryOut["trend"][number];
export type QualitySliceRow = QualitySummaryOut["by_datasource"][number];
export type QualityPromptSliceRow = QualitySummaryOut["by_prompt_version"][number];

/** 轮次质量总览(四态占比/率值 + 按天趋势补零 + 数据源/prompt 切片) */
export function qualitySummary(days = 30): Promise<QualitySummaryOut> {
  return api.get<QualitySummaryOut>(`/system/chat-quality/summary?days=${days}`);
}

export interface QualityPageParams {
  page?: number;
  size?: number;
  status?: "SUCCESS" | "FAILED" | "REJECTED" | "NO_DATA";
  datasource_key?: string;
  start?: string;
  end?: string;
}

/** 轮次明细分页(时间倒序;状态/数据源/时间范围筛选) */
export function qualityPage(params: QualityPageParams): Promise<QualityPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  const suffix = qs.size > 0 ? `?${qs}` : "";
  return api.get<QualityPageOut>(`/system/chat-quality/page${suffix}`);
}
