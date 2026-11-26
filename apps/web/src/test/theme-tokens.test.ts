import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// 安全区来自设备环境,亮暗一致,不属于主题 token
const THEME_INVARIANT = new Set(["--kb-safe-bottom", "--kb-safe-top"]);

/** 取指定选择器块内声明的自定义属性名(按行取声明名,注释与值不参与)。 */
function blockTokens(css: string, selector: string): Set<string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`index.css 缺少 ${selector} 块`);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const names = body
    .split("\n")
    .map((line) => line.split("/*")[0]!.split(":")[0]!.trim())
    .filter((name) => name.startsWith("--"));
  return new Set(names);
}

/** 取指定选择器块内的十六进制 token 值(测试只覆盖纯色 token)。 */
function blockColors(css: string, selector: string): Map<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`index.css 缺少 ${selector} 块`);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  const out = new Map<string, string>();
  const tokenPattern = /^\s*(--[\w-]+)\s*:\s*(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}))\s*;/;
  for (const line of body.split("\n")) {
    const match = tokenPattern.exec(line.split("/*")[0]!);
    if (match) out.set(match[1]!, match[2]!);
  }
  return out;
}

function rgb(hex: string): [number, number, number] {
  const full = hex.length === 4 ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}` : hex;
  return [
    Number.parseInt(full.slice(1, 3), 16),
    Number.parseInt(full.slice(3, 5), 16),
    Number.parseInt(full.slice(5, 7), 16),
  ];
}

function blend(fg: [number, number, number], bg: [number, number, number], alpha: number) {
  return fg.map((value, i) => Math.round(alpha * value + (1 - alpha) * bg[i]!)) as [
    number,
    number,
    number,
  ];
}

function luminance(color: [number, number, number]): number {
  const [r, g, b] = color.map((value) => {
    const v = value / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: [number, number, number], b: [number, number, number]): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

describe("index.css 主题 token", () => {
  const css = readFileSync(fileURLToPath(new URL("../index.css", import.meta.url)), "utf8");

  it(":root 的每个主题 token 都有 .dark 覆盖(宪法 Article 3/10)", () => {
    const root = blockTokens(css, ":root");
    const dark = blockTokens(css, ".dark");
    const missing = [...root].filter((token) => !dark.has(token) && !THEME_INVARIANT.has(token));
    expect(missing).toEqual([]);
  });

  it("代码高亮 token 与代码块背景对比度不低于 4.5:1", () => {
    for (const selector of [":root", ".dark"]) {
      const colors = blockColors(css, selector);
      const page = colors.get("--background");
      const muted = colors.get("--muted");
      if (!page || !muted) throw new Error(`${selector} 缺少 --background/--muted`);
      // 聊天页代码块实际叠在 --background 上,按最差背景测对比度
      const background = blend(rgb(muted), rgb(page), 0.7);
      for (const [name, value] of colors) {
        if (!name.startsWith("--code-")) continue;
        expect(contrast(rgb(value), background), `${selector} ${name}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
