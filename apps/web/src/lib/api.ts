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

const BASE = "/api";
const REFRESH_KEY = "km.refresh";

/* 当前租户标识(api 层直读持久化键,单点注入免遗漏;头对非 chat 端点无语义影响) */
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
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...tenantHeader(),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init?.headers,
    },
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 登录/刷新自身的 401 不重放(避免递归)
  const replayable = path !== "/auth/login" && path !== "/auth/refresh";
  let resp = await doFetch(path, init);
  if (resp.status === 401 && replayable && (await tryRefresh())) {
    resp = await doFetch(path, init);
  }
  if (!resp.ok) {
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
    throw err;
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
