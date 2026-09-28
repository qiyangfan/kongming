import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath, URL } from "node:url";
import { describe, expect, it } from "vitest";

/* 体积治理回归护栏(066.2.3):echarts/zrender 渲染引擎约 250 kB,只允许经
   动态 import 的按需 chunk 进入依赖图。静态 import 若逃逸进首屏入口链,
   构建无任何报错(它会静默进主包)——故以静态源码扫描钉死边界:
   允许静态 import 的只有按需注册单源 lib/echarts.ts 与其唯一实现
   chart-view-impl(仅由 chart-view.tsx 动态 import 引用)。 */

const SRC = fileURLToPath(new URL("../", import.meta.url));

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
  const re = /(^|\n)\s*import\s+(?:[^"'()\n]*?\s+from\s+)?["'](echarts|zrender)[^"']*["']/g;
  for (const m of source.matchAll(re)) hits.push(m[2]);
  return hits;
}

const ALLOWED_STATIC = new Set([
  "lib/echarts.ts", // 按需注册单源(echarts/core + 四系列 + CanvasRenderer)
  "components/chart-view-impl.tsx", // 唯一实现,仅动态块可达
]);

describe("echarts/zrender 动态块边界(静态守卫)", () => {
  const files = walk(SRC);
  const offenders: Record<string, string[]> = {};

  it("源码树可扫描(守卫非空转)", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("静态 import echarts/zrender 的文件恰为白名单", () => {
    for (const f of files) {
      const rel = relative(SRC, f).replaceAll("\\", "/");
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
