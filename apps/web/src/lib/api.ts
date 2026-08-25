/* API 客户端。类型契约由 ADR-005 管线生成:
   pnpm contracts:gen → api-types.gen.ts(禁手写接口类型)。

   令牌策略(ADR-003):
   - refresh token 存 localStorage(跨刷新会话恢复;XSS 暴露面见 002 design Risks)
   - access token 仅存内存;启动凭 refresh 单飞换发
   - 401 → 单飞刷新 → 重放原请求一次;刷新失败清态并通知会话终结
*/

import type { components } from "./api-types.gen";
import { TENANT_STORAGE_KEY } from "./tenant-store";

export type TokenPairOut = components["schemas"]["TokenPairOut"];
export type MeOut = components["schemas"]["MeOut"];
export type OptionsOut = components["schemas"]["OptionsOut"];
export type StartOut = components["schemas"]["StartOut"];
export type SessionItemOut = components["schemas"]["SessionItemOut"];
export type SessionMessageOut = components["schemas"]["SessionMessageOut"];
export type ChatQueryOut = components["schemas"]["ChatQueryOut"];
export type TenantRefOut = components["schemas"]["TenantRefOut"];
/* 017 流式事件 data 形态(make contracts 生成;契约见 openapi responses oneOf) */
export type StreamStageOut = components["schemas"]["StreamStageOut"];
export type StreamSqlOut = components["schemas"]["StreamSqlOut"];
export type StreamSummaryOut = components["schemas"]["StreamSummaryOut"];
export type StreamErrorOut = components["schemas"]["StreamErrorOut"];
/* 019 tool-calling:tool_call 事件 + 待确认调用 + 确认回执 */
export type StreamToolCallOut = components["schemas"]["StreamToolCallOut"];
export type PendingToolCallOut = components["schemas"]["PendingToolCallOut"];
export type ToolConfirmOut = components["schemas"]["ToolConfirmOut"];
export type ToolItemOut = components["schemas"]["ToolItemOut"];
export type ToolListOut = components["schemas"]["ToolListOut"];
export type ToolAuditItem = components["schemas"]["ToolAuditItem"];
export type ToolAuditPageOut = components["schemas"]["ToolAuditPageOut"];

const BASE = "/api";
const REFRESH_KEY = "km.refresh";

/* 当前租户标识(api 层直读持久化键,单点注入免遗漏;头对 chat 系端点有语义,
   对多数管理端点无语义——例外:008 数据源 options 接口为用户面,同构消费该头) */
function tenantHeader(): Record<string, string> {
  const t = localStorage.getItem(TENANT_STORAGE_KEY);
  return t ? { "X-Tenant-Id": t } : {};
}

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
const sessionClearedListeners = new Set<() => void>();

export function onSessionCleared(cb: () => void): () => void {
  sessionClearedListeners.add(cb);
  return () => sessionClearedListeners.delete(cb);
}

export function setTokens(access: string, refresh: string): void {
  accessToken = access;
  localStorage.setItem(REFRESH_KEY, refresh);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function clearTokens(): void {
  const had = accessToken !== null || localStorage.getItem(REFRESH_KEY) !== null;
  accessToken = null;
  localStorage.removeItem(REFRESH_KEY);
  if (had) sessionClearedListeners.forEach((cb) => cb());
}

/* 单飞刷新:并发 401 只触发一次 /auth/refresh */
export function tryRefresh(): Promise<boolean> {
  refreshing ??= (async () => {
    const rt = localStorage.getItem(REFRESH_KEY);
    if (!rt) return false;
    try {
      const resp = await fetch(`${BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!resp.ok) {
        clearTokens();
        return false;
      }
      const pair = (await resp.json()) as TokenPairOut;
      setTokens(pair.access_token, pair.refresh_token);
      return true;
    } catch {
      return false; // 网络异常:保留本地态,由下次请求再试
    }
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

function doFetch(path: string, init?: RequestInit): Promise<Response> {
  // multipart(FormData)不设 Content-Type——浏览器须自动生成 boundary,手设会致
  // 服务端解析不出表单字段(009 冒烟实证);JSON 请求显式声明
  const isForm = init?.body instanceof FormData;
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...tenantHeader(),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init?.headers,
    },
  });
}

async function toStatusError(resp: Response): Promise<Error & { status?: number }> {
  // 优先取 FastAPI {detail} 文案,原始 JSON 体不直接抛给用户
  let detail = `HTTP ${resp.status}`;
  try {
    const body: unknown = await resp.json();
    if (body && typeof body === "object" && "detail" in body) {
      const d = (body as { detail: unknown }).detail;
      detail = typeof d === "string" ? d : detail;
    }
  } catch {
    /* 非 JSON 响应,保留 HTTP 状态码文案 */
  }
  const err = new Error(detail) as Error & { status?: number };
  err.status = resp.status;
  return err;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 登录/刷新自身的 401 不重放(避免递归)
  const replayable = path !== "/auth/login" && path !== "/auth/refresh";
  let resp = await doFetch(path, init);
  if (resp.status === 401 && replayable && (await tryRefresh())) {
    resp = await doFetch(path, init);
  }
  if (!resp.ok) throw await toStatusError(resp);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** 文件下载类请求(010 导出):401 重放与错误 detail 与 request 同构;
    成功返回原始 Response,由调用方取 blob(须先辨 JSON 错误形态,上游实证) */
async function postBlob(path: string, body: unknown): Promise<Response> {
  const init = { method: "POST", body: JSON.stringify(body) };
  let resp = await doFetch(path, init);
  if (resp.status === 401 && (await tryRefresh())) {
    resp = await doFetch(path, init);
  }
  if (!resp.ok) throw await toStatusError(resp);
  return resp;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  /** multipart 上传(009 知识库文档);浏览器自动设 boundary,勿手写 Content-Type */
  postForm: <T>(path: string, form: FormData) =>
    request<T>(path, { method: "POST", body: form }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  postBlob,
};

/* ============ 流式问答(017 query-streaming) ============ */

export interface StreamHandlers {
  onStage?: (d: StreamStageOut) => void;
  onSql?: (d: StreamSqlOut) => void;
  onSummary?: (d: StreamSummaryOut) => void;
  onToolCall?: (d: StreamToolCallOut) => void;
  onResult?: (d: ChatQueryOut) => void;
  onError?: (d: StreamErrorOut) => void;
}

/** 解析一个 SSE 帧块(event:/data: 行;注释行忽略)并分发;畸形 data 静默跳帧 */
function dispatchSseBlock(block: string, handlers: StreamHandlers): boolean {
  let event = "";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) return false; // 心跳注释行
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!event || dataLines.length === 0) return false;
  let data: unknown;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    return false; // 畸形帧跳过,流不断
  }
  switch (event) {
    case "stage":
      handlers.onStage?.(data as StreamStageOut);
      return false;
    case "sql":
      handlers.onSql?.(data as StreamSqlOut);
      return false;
    case "summary":
      handlers.onSummary?.(data as StreamSummaryOut);
      return false;
    case "tool_call":
      handlers.onToolCall?.(data as StreamToolCallOut);
      return false;
    case "result":
      handlers.onResult?.(data as ChatQueryOut);
      return true;
    case "error":
      handlers.onError?.(data as StreamErrorOut);
      return true;
    default:
      return false; // 未知事件忽略(前向兼容)
  }
}

/**
 * 流式问答(017):fetch + ReadableStream 手写 SSE 分帧(EventSource 不支持
 * POST/Bearer/X-Tenant-Id)。鉴权头/401 单飞重放与 request 同构;流开始前
 * 错误(4xx/5xx)抛 status Error(调用方按常规错误呈现);流正常结束但未收到
 * 终态事件也抛错(防气泡永等待);主动 abort(signal)静默返回不回调错误。
 */
export async function streamChatQuery(
  body: unknown,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const init: RequestInit = { method: "POST", body: JSON.stringify(body), signal };
  let resp = await doFetch("/chat/query/stream", init);
  if (resp.status === 401 && (await tryRefresh())) {
    resp = await doFetch("/chat/query/stream", init);
  }
  if (!resp.ok) throw await toStatusError(resp);
  const reader = resp.body?.getReader();
  if (!reader) throw new Error("流式响应不可用");
  const decoder = new TextDecoder();
  let buffer = "";
  let terminated = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // SSE 规范行尾 CRLF/LF 皆合法:剥除 CR 消除跨 chunk 的 \r\n 分裂边界
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, "");
      // SSE 帧边界:空行
      for (let idx = buffer.indexOf("\n\n"); idx >= 0; idx = buffer.indexOf("\n\n")) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (dispatchSseBlock(block, handlers)) terminated = true;
      }
    }
  } catch (err) {
    if (signal.aborted) return; // 主动取消:静默终止(迟到事件由守卫丢弃)
    throw err instanceof Error ? err : new Error("连接中断");
  } finally {
    void reader.cancel().catch(() => undefined);
  }
  if (!terminated) throw new Error("连接中断,答案未完整返回");
}
