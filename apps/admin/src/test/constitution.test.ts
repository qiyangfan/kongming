import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// 管理台前端宪法可机判部分:色值只走 token、业务代码不裸用原生控件、无 emoji、
// 无 window.confirm、仅 index.css 一张样式表。
const SRC = fileURLToPath(new URL("..", import.meta.url));
const HEX = /#[0-9a-fA-F]{3,8}\b/g;
const RAW_CONTROL = /<(?:button|input|select|textarea)[\s/>]/g;
const NATIVE_DIALOG = /window\.(?:confirm|alert)\s*\(/g;
const EMOJI = /\p{Emoji_Presentation}/gu;

function productionFiles(dir: string): string[] {
  return readdirSync(dir, { recursive: true })
    .map(String)
    .filter((name) => name.endsWith(".ts") || name.endsWith(".tsx"))
    .filter((name) => !name.includes(".test.") && !name.includes(".gen."))
    .map((name) => path.join(dir, name));
}

function scan(files: string[], pattern: RegExp): string[] {
  const hits: string[] = [];
  for (const file of files) {
    const rel = path.relative(SRC, file);
    const text = readFileSync(file, "utf8");
    for (const match of text.matchAll(pattern)) {
      hits.push(`${rel}:${text.slice(0, match.index ?? 0).split("\n").length} ${match[0]}`);
    }
  }
  return hits;
}

describe("管理台前端宪法", () => {
  const files = productionFiles(path.join(SRC));

  it("生产源码不硬编码十六进制色值", () => {
    expect(scan(files, HEX)).toEqual([]);
  });

  it("业务代码不直接使用原生表单控件", () => {
    const hits: string[] = [];
    for (const file of productionFiles(path.join(SRC, "pages"))) {
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(RAW_CONTROL)) {
        const index = match.index ?? 0;
        const tag = text.slice(index, index + 300);
        // 隐藏 file input 只作按钮触发管道,不是可见交互件
        if (
          tag.startsWith("<input") &&
          tag.includes('type="file"') &&
          tag.includes('className="hidden"')
        ) {
          continue;
        }
        hits.push(
          `${path.relative(SRC, file)}:${text.slice(0, index).split("\n").length} ${match[0]}`,
        );
      }
    }
    expect(hits).toEqual([]);
  });

  it("源码不使用 emoji 图标", () => {
    expect(scan(files, EMOJI)).toEqual([]);
  });

  it("禁用原生 confirm/alert", () => {
    expect(scan(files, NATIVE_DIALOG)).toEqual([]);
  });

  it("不新增样式表(token 单源 index.css)", () => {
    const css = readdirSync(SRC, { recursive: true })
      .map(String)
      .filter((name) => name.endsWith(".css"))
      .filter((name) => name !== "index.css");
    expect(css).toEqual([]);
  });
});
