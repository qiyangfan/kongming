import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath, URL } from "node:url";
import { describe, expect, it } from "vitest";

/* 静态检查保证 ECharts 仅从动态导入的图表边界进入应用，避免它进入首屏主包。 */

const SRC = fileURLToPath(new URL("../../", import.meta.url));
const PACKAGES = [
  fileURLToPath(new URL("../../../../../packages/api-client/src", import.meta.url)),
  fileURLToPath(new URL("../../../../../packages/ui/src", import.meta.url)),
];

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (/\.tsx?$/.test(name)) out.push(p);
  }
  return out;
}

/** 静态 import(形如 import … from "mod" 或 import "mod"),排除 import( … 动态形态 */
function staticEngineImports(source: string): string[] {
  const hits: string[] = [];
  for (const m of source.matchAll(/(?:^|\n)[ \t]*import\b(?!\s*\()/g)) {
    // 声明子句按分号或换行收口,避免跨语句误判;引号内即模块名
    const rest = source.slice(m.index + m[0].length, m.index + m[0].length + 500);
    const end = rest.indexOf(";");
    // 无分号(ASI 风格)时取满窗口而非首行:多行 import 的模块名可能在后几行,
    // 宁可多报(守卫失败方向安全)不可漏报
    const clause = end >= 0 ? rest.slice(0, end) : rest;
    for (const q of clause.matchAll(/["']([^"']+)["']/g)) {
      const mod = q[1] ?? "";
      if (/^(echarts|zrender)(\/|$)/.test(mod)) hits.push(mod);
    }
  }
  return hits;
}

const ALLOWED_STATIC = new Set([
  "packages/api-client/src/echarts.ts", // 按需注册单源(echarts/core + 四系列 + CanvasRenderer)
  "components/chart-view-impl.tsx", // 唯一实现,仅动态块可达
]);

describe("echarts/zrender 动态块边界(静态守卫)", () => {
  const files = [...walk(SRC), ...PACKAGES.flatMap((p) => walk(p))];
  const offenders: Record<string, string[]> = {};

  it("源码树可扫描(守卫非空转)", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("静态 import echarts/zrender 的文件恰为白名单", () => {
    for (const f of files) {
      const rel = relative(SRC, f).startsWith("..")
        ? `packages/${relative(fileURLToPath(new URL("../../../../../packages", import.meta.url)), f).replaceAll("\\", "/")}`
        : relative(SRC, f).replaceAll("\\", "/");
      const hits = staticEngineImports(readFileSync(f, "utf8"));
      if (hits.length > 0 && !ALLOWED_STATIC.has(rel)) offenders[rel] = hits;
    }
    expect(offenders).toEqual({});
  });

  it("chart-view.tsx 经动态 import 引用实现(懒加载边界存在)", () => {
    const src = readFileSync(join(SRC, "components/chart-view.tsx"), "utf8");
    expect(src).toMatch(/lazy\(\(\)\s*=>\s*import\("\.\/chart-view-impl"\)\)/);
  });

  it("首屏入口 main.tsx 不含任何 echarts/zrender import(动态或静态)", () => {
    const src = readFileSync(join(SRC, "main.tsx"), "utf8");
    expect(/\b(echarts|zrender)\b/.test(src.replace(/\/\*[\s\S]*?\*\//g, ""))).toBe(false);
  });
});
