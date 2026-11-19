/* 问答质量页的徽章口径与筛选参数拼装(纯模块,抽屉与列表共用)。 */

import type { StatusMeta } from "@/components/status-badge";

/* 主张重要度/支撑分级徽章口径(与后端公式取值域同源) */
export const IMPORTANCE_META: StatusMeta = {
  critical: { label: "核心", variant: "destructive" },
  normal: { label: "重要", variant: "secondary" },
  minor: { label: "补充", variant: "outline" },
};
export const SUPPORT_META: StatusMeta = {
  fully_supported: { label: "有据", variant: "secondary" },
  partially_supported: { label: "部分有据", variant: "outline" },
  unsupported: { label: "无据", variant: "destructive" },
};
/* 兜底轮分类徽章(spec「兜底轮分类观测」;空=非兜底轮) */
export const FALLBACK_META: StatusMeta = {
  CHITCHAT: { label: "闲聊", variant: "outline" },
  CLARIFY: { label: "澄清", variant: "outline" },
};

/* 轮次四态徽章;NO_DATA 是知识未覆盖的中性形态,不用失败色 */
export const ROUND_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  NO_DATA: { label: "未覆盖", variant: "outline" },
  REJECTED: { label: "已拦截", variant: "destructive" },
  FAILED: { label: "失败", variant: "destructive" },
};

type QualityFilters = {
  status: string;
  datasourceKey: string;
  fallback: string;
};

// 空筛选值不入参;明细分页与 CSV 导出共用同一组筛选项
export function filterParams(f: QualityFilters) {
  return {
    ...(f.status ? { status: f.status as "SUCCESS" | "FAILED" | "REJECTED" | "NO_DATA" } : {}),
    ...(f.datasourceKey ? { datasource_key: f.datasourceKey } : {}),
    ...(f.fallback ? { fallback: f.fallback as "CHITCHAT" | "CLARIFY" | "none" } : {}),
  };
}

// CSV 下载:锚点入 DOM 再点击,Safari 需要在延迟后才 revoke 对象 URL
export async function downloadCsv(
  url: string,
  filename: string,
  fetchBlob: (u: string) => Promise<Blob>,
) {
  const blob = await fetchBlob(url);
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
}
