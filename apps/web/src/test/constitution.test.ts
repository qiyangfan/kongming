import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// 前端宪法 Article 2/3/6 中可机器判定的部分:色值只走 token、基础控件只用
// components/ui、图标不用 emoji。样式层(index.css)是 token 单源,不在扫描范围。
const SRC = fileURLToPath(new URL("..", import.meta.url));

// 第三方身份提供商品牌图标必须保留官方色,不能换成本主题 token;
// 只豁免这一个文件,其余场景新增硬编码色值即失败。
const HEX_ALLOWED = new Set([path.join("pages", "Login.tsx")]);

const HEX = /#[0-9a-fA-F]{3,8}\b/g;
const RAW_CONTROL = /<(?:button|input|select|textarea)[\s/>]/g;
const NATIVE_DIALOG = /window\.(?:confirm|alert)\s*\(/g;
// 只拦默认以 emoji 形态呈现的字符(😀/🚀);↔ 这类文本箭头在注释里是符号不是图标
const EMOJI = /\p{Emoji_Presentation}/gu;

// Article 6:样式语言只有 Tailwind 原子类,token 单源是 src/index.css,不新增样式表
const CSS_ALLOWED = "index.css";

/** 递归列出 ts/tsx 生产源文件(测试文件与生成物不参与)。 */
function productionFiles(dir: string): string[] {
  return readdirSync(dir, { recursive: true })
    .map(String)
    .filter((name) => name.endsWith(".ts") || name.endsWith(".tsx"))
    .filter((name) => !name.includes(".test.") && !name.includes(".gen."))
    .map((name) => path.join(dir, name));
}

/** 把匹配位置换算成 1 起始行号,失败信息可直接定位。 */
function lineOf(text: string, index: number): number {
  return text.slice(0, index).split("\n").length;
}

function scan(files: string[], pattern: RegExp): string[] {
  const hits: string[] = [];
  for (const file of files) {
    const rel = path.relative(SRC, file);
    const text = readFileSync(file, "utf8");
    for (const match of text.matchAll(pattern)) {
      hits.push(`${rel}:${lineOf(text, match.index ?? 0)} ${match[0]}`);
    }
  }
  return hits;
}

/** 原生控件扫描:隐藏 file input 只作按钮触发管道,不是可见交互件,按豁免跳过。 */
function rawControlViolations(files: string[]): string[] {
  const hits: string[] = [];
  for (const file of files) {
    const rel = path.relative(SRC, file);
    const text = readFileSync(file, "utf8");
    for (const match of text.matchAll(RAW_CONTROL)) {
      const index = match.index ?? 0;
      const tag = text.slice(index, index + 300);
      if (
        tag.startsWith("<input") &&
        tag.includes('type="file"') &&
        tag.includes('className="hidden"')
      ) {
        continue;
      }
      hits.push(`${rel}:${lineOf(text, index)} ${match[0]}`);
    }
  }
  return hits;
}

describe("前端宪法", () => {
  it("生产源码不硬编码十六进制色值", () => {
    const files = productionFiles(SRC).filter((f) => !HEX_ALLOWED.has(path.relative(SRC, f)));
    expect(scan(files, HEX)).toEqual([]);
  });

  it("业务代码不直接使用原生表单控件", () => {
    const files = [
      ...productionFiles(path.join(SRC, "pages")),
      ...productionFiles(path.join(SRC, "components")).filter(
        (f) => !path.relative(path.join(SRC, "components"), f).startsWith(`ui${path.sep}`),
      ),
    ];
    expect(rawControlViolations(files)).toEqual([]);
  });

  it("源码不使用 emoji 图标", () => {
    expect(scan(productionFiles(SRC), EMOJI)).toEqual([]);
  });

  it("禁用原生 confirm/alert(破坏性操作走 AlertDialog)", () => {
    expect(scan(productionFiles(SRC), NATIVE_DIALOG)).toEqual([]);
  });

  it("不新增样式表(token 单源 index.css)", () => {
    const css = readdirSync(SRC, { recursive: true })
      .map(String)
      .filter((name) => name.endsWith(".css"))
      .filter((name) => name !== CSS_ALLOWED);
    expect(css).toEqual([]);
  });
});
