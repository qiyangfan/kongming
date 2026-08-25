/* store 域 API 封装(033)。类型契约由 ADR-005 管线生成(禁手写)。 */

import type { components } from "./api-types.gen";
import { api } from "./api";

export type StoreBotItem = components["schemas"]["StoreBotOut"];
export type StoreBotDetail = components["schemas"]["StoreBotDetailOut"];

export function listStoreBots(q: string, sort: string): Promise<StoreBotItem[]> {
  const qs = new URLSearchParams({ q, sort });
  return api.get<StoreBotItem[]>(`/store/bots?${qs}`);
}

export function getStoreBot(key: string): Promise<StoreBotDetail> {
  return api.get<StoreBotDetail>(`/store/bots/${key}`);
}

export function addFavorite(key: string): Promise<{ favorited: boolean }> {
  return api.post(`/store/bots/${key}/favorite`, {});
}

export function removeFavorite(key: string): Promise<{ favorited: boolean }> {
  return api.delete(`/store/bots/${key}/favorite`);
}

export function listFavorites(): Promise<StoreBotItem[]> {
  return api.get<StoreBotItem[]>("/store/favorites");
}

export function rateBot(key: string, score: number): Promise<{ ok: boolean }> {
  return api.put(`/store/bots/${key}/rating`, { score });
}

export function runStoreBot(
  key: string,
  question: string,
): Promise<components["schemas"]["StoreRunOut"]> {
  return api.post(`/store/bots/${key}/run`, { question });
}

export function publishBot(
  id: string,
  greeting: string,
  suggestions: string[],
): Promise<{ ok: boolean }> {
  return api.post(`/system/bots/${id}/publish`, { greeting, suggestions });
}

export function unpublishBot(id: string): Promise<{ ok: boolean }> {
  return api.post(`/system/bots/${id}/unpublish`, {});
}
