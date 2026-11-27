import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** 读取设计 token 计算值(canvas/图表取色用)。全站单源,禁再各文件复制本函数(宪法 Article 3)。 */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

/**
 * 弱类型载荷转展示文本:对象/数组走 JSON,避免直接 String() 渲染成 [object Object]。
 * null/undefined 统一为空串,调用方按需补占位符。
 */
export function cellText(value: unknown): string {
  if (value === null) return "";
  switch (typeof value) {
    case "string":
      return value;
    case "number":
    case "boolean":
    case "bigint":
      return String(value);
    case "object":
      return JSON.stringify(value);
    case "undefined":
    case "function":
    case "symbol":
      return "";
  }
  return "";
}
