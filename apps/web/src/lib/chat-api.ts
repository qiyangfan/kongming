/* chat 域 API 封装(010 会话体验深化)。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api, type ChatQueryOut, type SessionItemOut } from "./api";

export type ExplainOut = components["schemas"]["ExplainOut"];
export type ArtifactOut = components["schemas"]["ArtifactOut"];
export type AttachmentOut = components["schemas"]["AttachmentOut"];
export type SuggestionsOut = components["schemas"]["SuggestionsOut"];

/** 对话冷启动建议问题(039):按当前数据源获取;空清单=静默隐藏(生成失败/不可用) */
export function getSuggestions(datasourceKey: string): Promise<SuggestionsOut> {
  return api.get<SuggestionsOut>(
    `/chat/suggestions?datasource_key=${encodeURIComponent(datasourceKey)}`,
  );
}

/** SQL 产物再执行(026):message_id 归属校验 + 六层防御全量,结果落新消息 */
export function rerunArtifact(messageId: string, sql: string): Promise<ChatQueryOut> {
  return api.post<ChatQueryOut>("/chat/artifacts/rerun", { message_id: messageId, sql });
}

/** 图片附件上传(030):multipart,返回 attachment_id 供问答引用 */
export async function uploadAttachment(file: File): Promise<AttachmentOut> {
  const fd = new FormData();
  fd.append("file", file);
  return api.postForm("/chat/attachments", fd);
}

/** 114 语音转写:录音 blob → 文本(即转即弃;503=未启用模型,文案面向用户) */
export async function transcribeAudio(blob: Blob): Promise<string> {
  const fd = new FormData();
  fd.append("file", blob, "recording.webm");
  const out = await api.postForm<{ text: string }>("/chat/stt", fd);
  return out.text;
}

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
  const filename = parseDispositionFilename(resp.headers.get("content-disposition")) ?? fallback;
  downloadBlob(blob, filename);
}
