/* 租户上下文单源(web-nav spec「租户切换」):
   持久化键与 api.ts 共享;store 只管状态与校验,切库后的查询失效与导航由调用方处理。 */

import { create } from "zustand";

import type { TenantRefOut } from "./api";

export const TENANT_STORAGE_KEY = "km.tenant";

interface TenantState {
  /** 当前租户 key;null = 尚未 bootstrap(登录前) */
  current: string | null;
  /** 可切换租户全集(/auth/me 事实) */
  available: TenantRefOut[];
  /** 登录/bootstrap 后校准:持久值仍在绑定列表则沿用,否则取首项 */
  calibrate: (tenants: TenantRefOut[]) => void;
  switchTo: (key: string) => void;
  clear: () => void;
}

export const useTenant = create<TenantState>((set, get) => ({
  current: localStorage.getItem(TENANT_STORAGE_KEY),
  available: [],

  calibrate: (tenants) => {
    const persisted = get().current;
    const valid = tenants.some((t) => t.tenant_key === persisted);
    const next = tenants.length === 0 ? null : valid ? persisted : tenants[0].tenant_key;
    set({ available: tenants, current: next });
    if (next === null) localStorage.removeItem(TENANT_STORAGE_KEY);
    else localStorage.setItem(TENANT_STORAGE_KEY, next);
  },

  switchTo: (key) => {
    if (!get().available.some((t) => t.tenant_key === key)) return;
    localStorage.setItem(TENANT_STORAGE_KEY, key);
    set({ current: key });
  },

  clear: () => {
    localStorage.removeItem(TENANT_STORAGE_KEY);
    set({ current: null, available: [] });
  },
}));
