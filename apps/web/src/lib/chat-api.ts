/* chat 域 API 封装(010 会话体验深化)。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api, type SessionItemOut } from "./api";

export type ExplainOut = components["schemas"]["ExplainOut"];

/** 会话列表(010 搜索):q 非空时服务端按标题/user 问句 ILIKE 匹配 */
export function listSessions(q?: string): Promise<SessionItemOut[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return api.get<SessionItemOut[]>(`/chat/sessions${qs}`);
}

/** 重命名会话(010);成功返回更新后的列表项 */
export function renameSession(id: string, title: string): Promise<SessionItemOut> {
  return api.patch<SessionItemOut>(`/chat/sessions/${id}`, { title });
}

/** 执行计划(010):message_id 引用形态,客户端不提交 SQL */
export function explainPlan(messageId: string): Promise<ExplainOut> {
  return api.post<ExplainOut>("/chat/explain", { message_id: messageId });
}

/** Content-Disposition 的 filename*(RFC 5987)解析;取不到返回 null 由调用方兜底 */
function parseDispositionFilename(disposition: string | null): string | null {
  if (!disposition) return null;
  const m = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  if (!m?.[1]) return null;
  try {
    return decodeURIComponent(m[1]);
  } catch {
    return null; // 畸形编码:回退本地生成名
  }
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  // R1-O1(Safari 坑):锚点须入 DOM 且延迟回收——click 后同步 revoke 会取消下载
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** 结果导出(010):所见即所导(传展示中的 columns+rows,服务端不重跑 SQL);
    文件名优先取 Content-Disposition(查询结果-<时间戳>),缺失回退本地生成 */
export async function exportResult(
  format: "csv" | "xlsx",
  columns: string[],
  rows: unknown[][],
): Promise<void> {
  const resp = await api.postBlob("/chat/export", { format, columns, rows });
  // JSON 形态即错误应答(上游实证:不分辨会下载垃圾文件)
  if (resp.headers.get("content-type")?.includes("application/json")) {
    const body: unknown = await resp.json();
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : "导出失败";
    throw new Error(detail);
  }
  const blob = await resp.blob();
  const ts = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const fallback = `查询结果-${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}-${pad(ts.getHours())}${pad(ts.getMinutes())}${pad(ts.getSeconds())}.${format}`;
  const filename =
    parseDispositionFilename(resp.headers.get("content-disposition")) ?? fallback;
  downloadBlob(blob, filename);
}
