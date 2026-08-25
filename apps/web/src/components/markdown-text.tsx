/* 极简安全 Markdown 渲染(026 conversation-artifacts document 卡):
   零依赖(技术栈锁定,不引 react-markdown——proposal 依赖节未放行新库,
   走 Token 化 → React 元素路线),不经 dangerouslySetInnerHTML,原始 HTML
   一律按字面文本展示(spec「Markdown 渲染不解析原始 HTML」天然满足)。
   支持子集(覆盖 LLM 摘要现实形态):段落/换行、- 与 1. 列表、
   **粗体**、`行内代码`、#/##/### 标题;其余记号原样呈现,宁朴不错。 */

import type { ReactNode } from "react";

/* 行内记号:`code` 优先切分(代码段内的 ** 不再解析),再切 **bold**;
   未闭合记号(孤立 ` 或 **)正则不命中,自然按字面文本落出 */
const INLINE_RE = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;

function renderInline(text: string): ReactNode[] {
  return text.split(INLINE_RE).map((part, i) => {
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={i} className="rounded bg-muted/70 px-1 py-0.5 font-mono text-[0.85em]">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} className="font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return part;
  });
}

const HEADING_RE = /^(#{1,3})\s+(.*)$/;
const UL_RE = /^[-*]\s+(.*)$/;
const OL_RE = /^\d{1,3}[.)]\s+(.*)$/;

const HEADING_CLASS: Record<number, string> = {
  1: "text-base font-semibold",
  2: "text-[15px] font-semibold",
  3: "text-sm font-semibold",
};

export function MarkdownText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split(/\r?\n/);
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    if (trimmed === "") {
      i += 1;
      continue;
    }
    const h = HEADING_RE.exec(trimmed);
    if (h) {
      blocks.push(
        <p key={key++} className={HEADING_CLASS[h[1].length]}>
          {renderInline(h[2])}
        </p>,
      );
      i += 1;
      continue;
    }
    if (UL_RE.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length) {
        const m = UL_RE.exec(lines[i].trim());
        if (!m) break;
        items.push(m[1]);
        i += 1;
      }
      blocks.push(
        <ul key={key++} className="list-disc space-y-0.5 pl-5">
          {items.map((it, j) => (
            <li key={j}>{renderInline(it)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (OL_RE.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length) {
        const m = OL_RE.exec(lines[i].trim());
        if (!m) break;
        items.push(m[1]);
        i += 1;
      }
      blocks.push(
        <ol key={key++} className="list-decimal space-y-0.5 pl-5">
          {items.map((it, j) => (
            <li key={j}>{renderInline(it)}</li>
          ))}
        </ol>,
      );
      continue;
    }
    // 普通段落:连续非空行归为一段,行内换行保留(摘要单换行=自然段习惯)
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !HEADING_RE.test(lines[i].trim()) &&
      !UL_RE.test(lines[i].trim()) &&
      !OL_RE.test(lines[i].trim())
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(
      <p key={key++}>
        {para.map((l, j) => (
          <span key={j}>
            {j > 0 && <br />}
            {renderInline(l)}
          </span>
        ))}
      </p>,
    );
  }
  return <div className="space-y-2">{blocks}</div>;
}
