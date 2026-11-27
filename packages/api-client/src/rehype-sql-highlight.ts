/* 围栏代码块 SQL 语法高亮:只注册 sql 语法,其余语言与无标注块原样渲染。 */

import type { Element, ElementContent, Root, RootContent } from "hast";
import { refractor } from "refractor/core";
import sql from "refractor/sql";

refractor.register(sql);

function classList(node: Element): string[] {
  const raw = node.properties.className;
  return Array.isArray(raw) ? raw.map(String) : [];
}

function textContent(node: Root | RootContent): string {
  if (node.type === "text") return node.value;
  if (!("children" in node)) return "";
  return node.children.map(textContent).join("");
}

function walk(
  node: Root | RootContent,
  visit: (node: Root | RootContent) => void,
): void {
  visit(node);
  if ("children" in node) {
    for (const child of node.children) walk(child, visit);
  }
}

export function rehypeSqlHighlight() {
  return (tree: Root): void => {
    walk(tree, (node) => {
      if (node.type !== "element" || node.tagName !== "code") return;
      if (!classList(node).includes("language-sql")) return;
      const code = textContent(node);
      if (!code.trim()) return;
      node.children = refractor.highlight(code, "sql")
        .children as ElementContent[];
    });
  };
}
