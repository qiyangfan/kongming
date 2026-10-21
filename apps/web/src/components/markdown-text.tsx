/* 使用 react-markdown 渲染 Markdown，不解析原始 HTML（ADR-009）。 */

import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

const COMPONENTS: Components = {
  h1: ({ children }) => <h1 className="text-base font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="text-md font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="text-sm font-semibold">{children}</h3>,
  // 四级及更深与三级同级呈现,避免小卡片内字号倒挂
  h4: ({ children }) => <h4 className="text-sm font-semibold">{children}</h4>,
  h5: ({ children }) => <h5 className="text-sm font-semibold">{children}</h5>,
  h6: ({ children }) => <h6 className="text-sm font-semibold">{children}</h6>,
  ul: ({ children }) => <ul className="list-disc space-y-0.5 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-0.5 pl-5">{children}</ol>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-primary underline underline-offset-2"
    >
      {children}
    </a>
  ),
  code({ node, className, children }) {
    // 围栏代码块(跨行)样式由 pre 承担底色与滚动;行内 code 走徽章样式
    const span = node?.position;
    if (span && span.start.line !== span.end.line) {
      return <code className={`block font-mono ${className ?? ""}`}>{children}</code>;
    }
    return (
      <code className="rounded bg-muted/70 px-1 py-0.5 font-mono text-code-inline">{children}</code>
    );
  },
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-md bg-muted/70 p-3 text-sm">{children}</pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-border pl-3 text-muted-foreground">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-border" />,
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-border px-2 py-1 text-left font-medium">{children}</th>
  ),
  td: ({ children }) => <td className="border-b border-border px-2 py-1 align-top">{children}</td>,
  img: ({ src, alt }) => (
    <img src={src} alt={alt ?? ""} loading="lazy" className="max-w-full rounded-md" />
  ),
};

export function MarkdownText({ text }: { text: string }) {
  // react-markdown 渲染根是 Fragment(无包裹 div):块元素直接落在本层,
  // space-y-2 由此作用于各块元素
  return (
    <div className="space-y-2">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
