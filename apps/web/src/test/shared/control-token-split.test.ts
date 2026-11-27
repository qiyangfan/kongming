import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/* 锁定控件色值令牌的职责：描边变化不得改变控件填充或开关轨道。 */

const SRC = fileURLToPath(new URL("../../", import.meta.url));
const UI_SRC = fileURLToPath(new URL("../../../../../packages/ui/src", import.meta.url));
// 令牌值一律从去注释后的共享主题取(令牌单源在 packages/ui/theme.css);
// 注释里出现过 "--border:" 这样的字面写法,不排除会污染取值
const css = [
  readFileSync(new URL("../../../../../packages/ui/theme.css", import.meta.url), "utf8"),
  readFileSync(new URL("../../index.css", import.meta.url), "utf8"),
]
  .join("\n")
  .replace(/\/\*[\s\S]*?\*\//g, "");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (/\.tsx$/.test(name)) out.push(p);
  }
  return out;
}

/** 取 :root 或 .dark 的声明块正文(首个匹配=令牌块本身) */
function tokenBlock(selector: RegExp): string {
  const m = selector.exec(css);
  if (!m) throw new Error(`index.css 未找到 ${selector} 令牌块`);
  return m[1] ?? "";
}

function tokenOf(block: string, name: string): string | null {
  const m = new RegExp(`--${name}:\\s*([^;]+);`).exec(block);
  return m?.[1]?.trim() ?? null;
}

/** 统计源码中填充/描边类的消费点,并列出仍用裸 bg-input 的文件(相对 src/) */
function scanInputClassUses(): { offenders: string[]; fillUses: number; borderUses: number } {
  const result = { offenders: [] as string[], fillUses: 0, borderUses: 0 };
  for (const file of [...walk(SRC), ...walk(UI_SRC)]) {
    const source = readFileSync(file, "utf8");
    result.fillUses += (source.match(/bg-input-fill/g) ?? []).length;
    result.borderUses += (source.match(/border-input/g) ?? []).length;
    if (/bg-input(?!-fill)/.test(source)) result.offenders.push(file);
  }
  return result;
}

describe("控件边界/填充/分隔线令牌三者分离(084/085)", () => {
  const blocks = {
    亮色: tokenBlock(/:root\s*\{([\s\S]*?)\n\}/),
    暗色: tokenBlock(/\.dark\s*\{([\s\S]*?)\n\}/),
  };

  for (const [态, block] of Object.entries(blocks)) {
    it(`${态}:控件边界不抄分隔线,填充不抄控件边界`, () => {
      const border = tokenOf(block, "border");
      const input = tokenOf(block, "input");
      const fill = tokenOf(block, "input-fill");
      expect(input, `${态}缺 --input`).not.toBeNull();
      expect(fill, `${态}缺 --input-fill(085 拆分出的第三语义)`).not.toBeNull();
      expect(input).not.toEqual(border);
      expect(fill).not.toEqual(input);
    });
  }

  it("填充令牌经 @theme 注册,否则 bg-input-fill 类不会生成", () => {
    expect(css).toMatch(/--color-input-fill:\s*var\(--input-fill\)/);
  });

  it("标准件内不存在裸 bg-input(填充只许读 --input-fill)", () => {
    // 拆分靠改指向而非删类:填充与描边两类消费点都必须仍存在
    const { offenders, fillUses, borderUses } = scanInputClassUses();
    expect(offenders).toEqual([]);
    expect(fillUses).toBeGreaterThan(0);
    expect(borderUses).toBeGreaterThan(0);
  });
});
