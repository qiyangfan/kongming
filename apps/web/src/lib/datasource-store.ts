/* 数据源上下文单源(008 design D7,与 tenant-store 同构):
   localStorage km.datasource;选项来自 options 接口(按租户),
   持久值失效(切租户/数据源被禁用或删除)回落首项(default 合成项恒在)。 */

import { create } from "zustand";

export const DATASOURCE_STORAGE_KEY = "km.datasource";
export const DEFAULT_DATASOURCE_KEY = "default";

interface DatasourceState {
  /** 当前数据源 key;default=内置默认路径(非 sys_datasource 行) */
  current: string;
  /** 选项就绪后校准:持久值仍在选项集则沿用,否则取首项 */
  calibrate: (keys: string[]) => void;
  switchTo: (key: string) => void;
  clear: () => void;
}

export const useDatasource = create<DatasourceState>((set, get) => ({
  current: localStorage.getItem(DATASOURCE_STORAGE_KEY) ?? DEFAULT_DATASOURCE_KEY,

  calibrate: (keys) => {
    const next = keys.includes(get().current) ? get().current : (keys[0] ?? DEFAULT_DATASOURCE_KEY);
    set({ current: next });
    localStorage.setItem(DATASOURCE_STORAGE_KEY, next);
  },

  switchTo: (key) => {
    localStorage.setItem(DATASOURCE_STORAGE_KEY, key);
    set({ current: key });
  },

  clear: () => {
    localStorage.removeItem(DATASOURCE_STORAGE_KEY);
    set({ current: DEFAULT_DATASOURCE_KEY });
  },
}));
