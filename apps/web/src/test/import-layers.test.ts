import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// 与后端 import-linter 同思路的轻量分层守卫:共享层不得反向依赖页面层
const SRC = fileURLToPath(new URL("..", import.meta.url));

const RULES: { from: string; forbid: string[] }[] = [
  { from: "components", forbid: ["pages"] },
  { from: "lib", forbid: ["components", "pages"] },
];

/** 递归列出目录下的 ts/tsx 生产源文件(测试文件允许引用上层做断言)。 */
function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { recursive: true })
    .map(String)
    .filter((name) => name.endsWith(".ts") || name.endsWith(".tsx"))
    .filter((name) => !name.includes(".test."))
    .map((name) => path.join(dir, name));
}

/** 把 import 说明符解析为 src 下的绝对路径;第三方裸模块返回 null。 */
function resolveSpec(file: string, spec: string): string | null {
  if (spec.startsWith("@/")) return path.join(SRC, spec.slice(2));
  if (spec.startsWith(".")) return path.resolve(path.dirname(file), spec);
  return null;
}

describe("模块分层", () => {
  it("components 不依赖 pages,lib 不依赖 components/pages", () => {
    const violations: string[] = [];
    for (const rule of RULES) {
      for (const file of sourceFiles(path.join(SRC, rule.from))) {
        const from = path.relative(SRC, file);
        for (const [, spec] of readFileSync(file, "utf8").matchAll(/from\s+"([^"]+)"/g)) {
          const resolved = resolveSpec(file, spec!);
          if (resolved === null) continue;
          const target = path.relative(SRC, resolved);
          if (rule.forbid.some((dir) => target === dir || target.startsWith(`${dir}${path.sep}`))) {
            violations.push(`${from} -> ${spec}`);
          }
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
