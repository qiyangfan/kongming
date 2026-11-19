/* 工作流的状态徽章映射、节点状态文案与输出截断工具。 */

import type { StatusMeta } from "@/components/status-badge";

// run/节点状态 → 徽章统一走 StatusBadge(未知值回落 destructive,即非 SUCCESS 一律红);
// SKIPPED 为节点级状态(条件门控/依赖跳过),run 终态不出现
export const RUN_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  FAILED: { label: "失败", variant: "destructive" },
  TIMEOUT: { label: "超时", variant: "destructive" },
  STEP_LIMIT: { label: "超步数", variant: "destructive" },
  SKIPPED: { label: "已跳过", variant: "outline" },
};

/** 新建工作流的空定义(画布初始态;JSON 态展示为 {"nodes": []}) */
export const EMPTY_DEFINITION: unknown = { nodes: [] };

/** 节点状态中文(与 RUN_STATUS_META 同源);未收录值回退原值 */
export function nodeStatusLabel(s: string): string {
  return RUN_STATUS_META[s]?.label ?? s;
}

// 触发源中文(后端枚举:MANUAL 人工端点 / API 外部通道);未收录值回退原值
const TRIGGER_SOURCE_LABELS: Record<string, string> = {
  MANUAL: "手动触发",
  API: "API 触发",
};

/** 触发源中文名;未收录值原样返回。 */
export function triggerSourceLabel(v: string): string {
  return TRIGGER_SOURCE_LABELS[v] ?? v;
}

// 渲染前截断字节数上限(引擎节点级截断兜底之外的第二道防线,防多 MB 文本整段进 DOM)
const OUTPUT_LIMIT = 64 * 1024;

/**
 * 节点输出转为可展示文本;outputs 为 null(未产出)返回 null——
 * stringify(null) 会得到无信息量的字面 "null"。
 */
export function formatOutputs(raw: unknown): string | null {
  if (raw == null) return null;
  const text = JSON.stringify(raw, null, 2);
  return text.length > OUTPUT_LIMIT
    ? text.slice(0, OUTPUT_LIMIT) + "\n…(截断,共 " + text.length + " 字符)"
    : text;
}
