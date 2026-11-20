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

describe("index.css 主题 token", () => {
  const css = readFileSync(fileURLToPath(new URL("../index.css", import.meta.url)), "utf8");

  it(":root 的每个主题 token 都有 .dark 覆盖(宪法 Article 3/10)", () => {
    const root = blockTokens(css, ":root");
    const dark = blockTokens(css, ".dark");
    const missing = [...root].filter((token) => !dark.has(token) && !THEME_INVARIANT.has(token));
    expect(missing).toEqual([]);
  });
});
