import { describe, expect, it } from "vitest";

import type { DeptNodeOut } from "./system-api";
import { flattenDeptTree } from "./dept-tree";

function node(id: string, name: string, children: DeptNodeOut[] = []): DeptNodeOut {
  return {
    id,
    parent_id: null,
    dept_name: name,
    tenant_id: "t1",
    sort: 0,
    status: "ACTIVE",
    children,
  };
}

describe("flattenDeptTree", () => {
  it("深度优先展开并带层级深度(父先于子)", () => {
    const tree = [node("a", "总部", [node("b", "子部", [node("c", "孙部")])])];
    expect(flattenDeptTree(tree)).toEqual([
      { id: "a", label: "总部", depth: 0 },
      { id: "b", label: "子部", depth: 1 },
      { id: "c", label: "孙部", depth: 2 },
    ]);
  });

  it("空/undefined 返回空数组(多选面板空态由此判定)", () => {
    expect(flattenDeptTree(undefined)).toEqual([]);
    expect(flattenDeptTree([])).toEqual([]);
  });
});
