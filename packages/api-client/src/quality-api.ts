/* 轮次质量观测域 API 封装。类型契约由 ADR-005 管线生成(禁手写);两端点同挂 system:chat-quality:view。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type QualitySummaryOut = components["schemas"]["QualitySummaryOut"];
export type QualityPageOut = components["schemas"]["QualityPageOut"];
export type QualityRoundItemOut = components["schemas"]["QualityRoundItemOut"];
type QualityScoreDetailOut = components["schemas"]["QualityScoreDetailOut"];
type QualityContextOut = components["schemas"]["QualityContextOut"];
export type QualityContextItemOut =
  components["schemas"]["QualityContextItemOut"];
type QualityStageTraceOut = components["schemas"]["QualityStageTraceOut"];
export type QualityTrendPoint = QualitySummaryOut["trend"][number];

/** 轮次质量总览(四态占比/率值 + 按天趋势补零 + 数据源/prompt 切片) */
export function qualitySummary(days = 30): Promise<QualitySummaryOut> {
  return api.get<QualitySummaryOut>(
    `/system/chat-quality/summary?days=${days}`,
  );
}

interface QualityPageParams {
  page?: number;
  size?: number;
  status?: "SUCCESS" | "FAILED" | "REJECTED" | "NO_DATA";
  datasource_key?: string;
  fallback?: "CHITCHAT" | "CLARIFY" | "none";
  start?: string;
  end?: string;
}

/** 轮次明细分页(时间倒序;状态/数据源/时间范围筛选) */
export function qualityPage(
  params: QualityPageParams,
): Promise<QualityPageOut> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  const suffix = qs.size > 0 ? `?${qs}` : "";
  return api.get<QualityPageOut>(`/system/chat-quality/page${suffix}`);
}

/** 单轮评分详情(维度分+主张分解;404=未评分或越权,同形不区分) */
export function qualityScoreDetail(
  messageId: string,
): Promise<QualityScoreDetailOut> {
  return api.get<QualityScoreDetailOut>(
    `/system/chat-quality/score/${messageId}`,
  );
}

/** 同会话轮次串联(锚点轮必含,时间升序;404=越权或不存在,同形不区分) */
export function qualityContext(messageId: string): Promise<QualityContextOut> {
  return api.get<QualityContextOut>(
    `/system/chat-quality/context?message_id=${encodeURIComponent(messageId)}`,
  );
}

/** 单轮节点阶段轨迹(null=同步轮/无帧流,如实为空;404=越权或不存在) */
export function qualityStageTrace(
  messageId: string,
): Promise<QualityStageTraceOut> {
  return api.get<QualityStageTraceOut>(
    `/system/chat-quality/stage-trace/${messageId}`,
  );
}

/** 轮次质量导出(page 同筛选;CSV;403=无权限) */
export function qualityExportUrl(params: QualityPageParams): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === null) continue;
    qs.set(k, String(v));
  }
  const suffix = qs.size > 0 ? `?${qs}` : "";
  return `/system/chat-quality/export${suffix}`;
}
