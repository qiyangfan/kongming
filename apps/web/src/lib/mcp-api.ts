/* mcp 域 API 封装(028)。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type McpServerOut = components["schemas"]["McpServerOut"];
export type McpTestOut = components["schemas"]["McpTestOut"];
export type McpSyncOut = components["schemas"]["McpSyncOut"];

export function listMcpServers(): Promise<McpServerOut[]> {
  return api.get<McpServerOut[]>("/system/mcp");
}

export function createMcpServer(body: {
  server_key: string;
  name: string;
  endpoint: string;
  headers: Record<string, string>;
}): Promise<McpServerOut> {
  return api.post<McpServerOut>("/system/mcp", body);
}

export function updateMcpServer(
  id: string,
  body: { name: string; endpoint: string; headers: Record<string, string | null> },
): Promise<McpServerOut> {
  return api.put<McpServerOut>(`/system/mcp/${id}`, body);
}

export function setMcpServerEnabled(id: string, enabled: boolean): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>(`/system/mcp/${id}/${enabled ? "enable" : "disable"}`, {});
}

export function deleteMcpServer(id: string): Promise<{ ok: boolean }> {
  return api.delete<{ ok: boolean }>(`/system/mcp/${id}`);
}

export function testMcpServer(id: string): Promise<McpTestOut> {
  return api.post<McpTestOut>(`/system/mcp/${id}/test`, {});
}

export function syncMcpServer(id: string): Promise<McpSyncOut> {
  return api.post<McpSyncOut>(`/system/mcp/${id}/sync`, {});
}
