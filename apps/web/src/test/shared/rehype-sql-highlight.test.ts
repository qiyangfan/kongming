import type { Element, Root, RootContent } from "hast";
import { refractor } from "refractor/core";
import { describe, expect, it } from "vitest";

import { rehypeSqlHighlight } from "@kongming/api-client/rehype-sql-highlight";

function codeElement(className: string[] | undefined, value: string): Element {
  return {
    type: "element",
    tagName: "code",
    properties: className ? { className } : {},
    children: [{ type: "text", value }],
  };
}

function runPlugin(code: Element): Element {
  const tree: Root = {
    type: "root",
    children: [{ type: "element", tagName: "pre", properties: {}, children: [code] }],
  };
  rehypeSqlHighlight()(tree);
  return code;
}

describe("rehypeSqlHighlight", () => {
  it("language-sql 围栏块产出 token 高亮节点", () => {
    const code = runPlugin(codeElement(["language-sql"], "SELECT * FROM t;"));
    const spans = code.children as Element[];
    expect(spans.length).toBeGreaterThan(1);
    const keyword = spans.find(
      (n) => n.type === "element" && (n.properties.className as string[]).includes("keyword"),
    );
    expect(keyword).toBeDefined();
  });

  it("只注册 sql 语法,不引入其他语言", () => {
    expect(refractor.registered("sql")).toBe(true);
    expect(refractor.registered("php")).toBe(false);
  });

  it("非 sql 语言保持原样", () => {
    const original: RootContent[] = [{ type: "text", value: "print(1)" }];
    const node = codeElement(["language-python"], "print(1)");
    const before = node.children;
    runPlugin(node);
    expect(node.children).toEqual(before);
    expect(node.children).toEqual(original);
  });

  it("无语言标注保持原样", () => {
    const node = codeElement(undefined, "SELECT 1");
    const before = node.children;
    runPlugin(node);
    expect(node.children).toBe(before);
  });

  it("空内容不处理", () => {
    const node = codeElement(["language-sql"], "  ");
    const before = node.children;
    runPlugin(node);
    expect(node.children).toBe(before);
  });
});
