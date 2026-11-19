/* 主题优先级为 localStorage 手动值、系统偏好、亮色；index.html 的防闪烁脚本须保持同一顺序。 */

import { create } from "zustand";

const THEME_STORAGE_KEY = "km-theme";

type Theme = "light" | "dark";

function readStored(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null; // 隐私模式极端配置:读不到按无手动值处理,功能降级不报错
  }
}

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** 契约解析(与 index.html 内联脚本同语义) */
function resolveTheme(): Theme {
  return readStored() ?? systemTheme();
}

function applyToDom(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

interface ThemeState {
  /** 当前生效主题(渲染态;跟随手动值或系统偏好) */
  theme: Theme;
  /** 手动切换:翻转生效主题,写入 localStorage 并从此优先于系统偏好 */
  toggle: () => void;
}

export const useTheme = create<ThemeState>((set, get) => ({
  theme: resolveTheme(),

  toggle: () => {
    const next: Theme = get().theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      /* 隐私模式下存储失败时仅影响持久化，当前会话仍生效。 */
    }
    applyToDom(next);
    set({ theme: next });
  },
}));

// 系统偏好实时跟随:有手动值时忽略系统变化(手动值优先,不逆转用户选择)
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
  if (readStored() !== null) return;
  const next: Theme = e.matches ? "dark" : "light";
  applyToDom(next);
  useTheme.setState({ theme: next });
});
