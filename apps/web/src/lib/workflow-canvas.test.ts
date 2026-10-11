import { describe, expect, it } from "vitest";

import {
  canvasToDag,
  dagToCanvas,
  missingRequiredKeys,
  nextNodeId,
  type CanvasGraph,
} from "./workflow-canvas";

/* 107 canvasState ⇄ DAG 互转性质测试:
   1) 合法定义往返无损(逐字段 deep-equal,含键缺省形态:无 depends_on 键、
      显式空 depends_on、config 键序);
   2) 不支持形态白名单检出(未知节点类型/未知字段/非法 on/悬挂依赖…),
      显式回落 JSON 态,绝不静默丢字段。 */

/** 构造无坐标干扰的图:入参定义 → 画布 → 回定义 */
function roundTrip(definition: unknown): unknown {
  const to = dagToCanvas(definition);
  if (!to.ok) throw new Error(`应当可表达: ${to.reasons.join("; ")}`);
  const back = canvasToDag(to.graph);
  if (!back.ok) throw new Error(`应当可回转: ${back.reasons.join("; ")}`);
  return back.definition;
}

describe("合法定义往返无损", () => {
  const cases: Record<string, unknown> = {
    "sql+transform 链(模板无 depends_on 键)": {
      nodes: [
        { id: "q", type: "sql", config: { sql: "SELECT 1" } },
        {
          id: "t",
          type: "transform",
          config: { template: "共 {{q.outputs.row_count}} 行" },
          depends_on: ["q"],
        },
      ],
    },
    "http 全字段": {
      nodes: [
        {
          id: "call",
          type: "http",
          config: {
            url: "https://api.example.com/x",
            method: "POST",
            headers: { "X-A": "1" },
            body: '{"k":1}',
          },
        },
      ],
    },
    "condition 双分支 on true/false": {
      nodes: [
        {
          id: "c",
          type: "condition",
          config: { left: "{{q.outputs.row_count}}", op: ">=", right: "10" },
          depends_on: ["q"],
        },
        {
          id: "big",
          type: "agent",
          config: { instruction: "深挖", on: "true" },
          depends_on: ["c", "q"],
        },
        {
          id: "small",
          type: "transform",
          config: { template: "少", on: "false" },
          depends_on: ["c"],
        },
        { id: "q", type: "sql", config: { sql: "SELECT count(*) c FROM t" } },
      ],
    },
    "loop 含子节点数组与迭代上限": {
      nodes: [
        { id: "q", type: "sql", config: { sql: "SELECT id FROM users" } },
        {
          id: "loop1",
          type: "loop",
          config: {
            items_ref: "q.outputs.rows",
            max_iterations: 10,
            body: [
              {
                id: "sub",
                type: "transform",
                config: { template: "{{_loop.item.id}}" },
                depends_on: [],
              },
            ],
          },
          depends_on: ["q"],
        },
      ],
    },
    "agent 带 datasource_key": {
      nodes: [
        {
          id: "a1",
          type: "agent",
          config: { instruction: "总结", datasource_key: "warehouse" },
        },
      ],
    },
    "显式空 depends_on 保留键": {
      nodes: [{ id: "q", type: "sql", config: { sql: "SELECT 1" }, depends_on: [] }],
    },
    "空 nodes 数组(新建初始态)": { nodes: [] },
    多前继混合依赖顺序保持: {
      nodes: [
        { id: "a", type: "sql", config: { sql: "SELECT 1" } },
        { id: "b", type: "sql", config: { sql: "SELECT 2" } },
        { id: "c", type: "sql", config: { sql: "SELECT 3" } },
        {
          id: "t",
          type: "transform",
          config: { template: "{{a.outputs.x}}{{b.outputs.y}}{{c.outputs.z}}" },
          depends_on: ["b", "a", "c"],
        },
      ],
    },
    "config 键序保持(datasource_key 在 sql 前)": {
      nodes: [
        {
          id: "q",
          type: "sql",
          config: { datasource_key: "d2", sql: "SELECT 1" },
        },
      ],
    },
  };

  for (const [name, definition] of Object.entries(cases)) {
    it(`${name} — 画布往返后逐字段一致`, () => {
      expect(roundTrip(definition)).toEqual(definition);
    });
  }

  it("坐标不进契约:带自定义坐标往返不影响定义", () => {
    const definition = cases["agent 带 datasource_key"];
    const to = dagToCanvas(definition, { a1: { x: 520, y: -80 } });
    expect(to.ok).toBe(true);
    if (!to.ok) return;
    expect(to.graph.nodes[0]?.position).toEqual({ x: 520, y: -80 });
    const back = canvasToDag(to.graph);
    expect(back).toEqual({ ok: true, definition });
  });

  it("坐标缺省走网格布局且不回写定义", () => {
    const definition = cases["多前继混合依赖顺序保持"];
    const to = dagToCanvas(definition);
    expect(to.ok).toBe(true);
    if (!to.ok) return;
    // 四节点网格坐标互不相同(可拖拽还原),坐标只活在画布态,定义零坐标字段
    const xs = new Set(to.graph.nodes.map((n) => `${n.position.x},${n.position.y}`));
    expect(xs.size).toBe(4);
    const back = roundTrip(definition);
    expect(back).toEqual(definition);
    expect(JSON.stringify(back)).not.toContain("position");
  });
});

describe("不支持形态白名单检出(明示回落 JSON 态)", () => {
  const unsupported: Record<string, unknown>[] = [
    { 顶层多余键: 1, nodes: [] } as unknown as Record<string, unknown>,
    { nodes: "not-array" } as unknown as Record<string, unknown>,
    { nodes: [{ id: "x", type: "python", config: {} }] } as unknown as Record<string, unknown>,
    {
      nodes: [{ id: "x", type: "sql", config: { sql: "1", custom_extra: 1 }, depends_on: [] }],
    } as unknown as Record<string, unknown>,
    {
      nodes: [{ id: "x", type: "sql", config: { sql: "1" }, label: "多余节点键" }],
    } as unknown as Record<string, unknown>,
    { nodes: [{ id: "x", type: "sql" }] } as unknown as Record<string, unknown>,
    { nodes: [{ id: "x", type: "sql", config: "not-object" }] } as unknown as Record<
      string,
      unknown
    >,
    { nodes: [{ id: "", type: "sql", config: {} }] } as unknown as Record<string, unknown>,
    { nodes: [{ id: " x ", type: "sql", config: {} }] } as unknown as Record<string, unknown>,
    {
      nodes: [
        { id: "x", type: "sql", config: {} },
        { id: "x", type: "transform", config: {} },
      ],
    },
    {
      nodes: [{ id: "x", type: "sql", config: { sql: "1" }, depends_on: ["ghost"] }],
    } as unknown as Record<string, unknown>,
    {
      nodes: [{ id: "x", type: "sql", config: { sql: "1" }, depends_on: ["x"] }],
    } as unknown as Record<string, unknown>,
    {
      nodes: [
        { id: "x", type: "sql", config: { sql: "1" }, depends_on: ["y", "y"] },
        { id: "y", type: "sql", config: { sql: "2" } },
      ],
    } as unknown as Record<string, unknown>,
    {
      nodes: [{ id: "x", type: "sql", config: { sql: "1", on: "yes" } }],
    } as unknown as Record<string, unknown>,
    {
      // on 值存在但无条件前驱:画布边上承载不了,静默丢弃 = 禁止
      nodes: [
        { id: "c", type: "sql", config: { sql: "1" } },
        { id: "x", type: "transform", config: { template: "t", on: "true" }, depends_on: ["c"] },
      ],
    } as unknown as Record<string, unknown>,
    {
      nodes: [{ id: "x", type: "sql", config: { sql: 1 } }, 42],
    } as unknown as Record<string, unknown>,
  ];

  for (const [i, definition] of unsupported.entries()) {
    it(`不支持形态 #${i} 显式拒绝且给出原因`, () => {
      const to = dagToCanvas(definition);
      expect(to.ok).toBe(false);
      if (to.ok) return;
      expect(to.reasons.length).toBeGreaterThan(0);
      expect(to.reasons[0]).toBeTruthy();
    });
  }
});

describe("画布 → 定义防御(UI 产出图)", () => {
  const nodeOf = (
    id: string,
    type: "sql" | "condition" | "transform",
    config: Record<string, unknown>,
  ) => ({
    id,
    type,
    config,
    position: { x: 0, y: 0 },
    depsExplicit: false,
  });

  it("删除条件入边后 config.on 被裁剪(边是 on 的唯一载体)", () => {
    const graph: CanvasGraph = {
      nodes: [
        nodeOf("c", "condition", { left: "1", op: "==", right: "1" }),
        nodeOf("t", "transform", { template: "x", on: "true" }),
      ],
      edges: [], // 条件入边已被用户删除
    };
    const back = canvasToDag(graph);
    expect(back.ok).toBe(true);
    if (!back.ok) return;
    const t = (
      back.definition as { nodes: { id: string; config: Record<string, unknown> }[] }
    ).nodes.find((n) => n.id === "t");
    expect(t?.config).toEqual({ template: "x" });
    expect("on" in (t?.config ?? {})).toBe(false);
  });

  it("同目标多条条件入边 on 值冲突 → 拒绝(防御,UI 不应产出)", () => {
    const graph: CanvasGraph = {
      nodes: [
        nodeOf("c1", "condition", { left: "1", op: "==", right: "1" }),
        nodeOf("c2", "condition", { left: "2", op: "==", right: "2" }),
        nodeOf("t", "transform", { template: "x" }),
      ],
      edges: [
        { id: "e1", source: "c1", target: "t", on: "true" },
        { id: "e2", source: "c2", target: "t", on: "false" },
      ],
    };
    expect(canvasToDag(graph).ok).toBe(false);
  });

  it("依赖顺序取自入边顺序;空依赖且非显式键时省略 depends_on", () => {
    const graph: CanvasGraph = {
      nodes: [nodeOf("a", "sql", { sql: "1" }), nodeOf("t", "transform", { template: "x" })],
      edges: [{ id: "e", source: "a", target: "t", on: null }],
    };
    const back = canvasToDag(graph);
    expect(back.ok).toBe(true);
    if (!back.ok) return;
    const nodes = (back.definition as { nodes: Record<string, unknown>[] }).nodes;
    expect(nodes[1]?.depends_on).toEqual(["a"]);
    expect("depends_on" in nodes[0]!).toBe(false);
  });

  it("空依赖但显式键 → 保留空数组", () => {
    const graph: CanvasGraph = {
      nodes: [{ ...nodeOf("a", "sql", { sql: "1" }), depsExplicit: true }],
      edges: [],
    };
    const back = canvasToDag(graph);
    expect(back.ok).toBe(true);
    if (!back.ok) return;
    expect((back.definition as { nodes: Record<string, unknown>[] }).nodes[0]).toEqual({
      id: "a",
      type: "sql",
      config: { sql: "1" },
      depends_on: [],
    });
  });
});

describe("辅助纯函数", () => {
  it("nextNodeId 跳过已占用且产出合法 id 形态", () => {
    const ids = [{ id: "sql" }, { id: "sql_2" }];
    expect(nextNodeId(ids, "sql")).toBe("sql_3");
    expect(nextNodeId([], "agent")).toBe("agent");
    expect(nextNodeId(ids, "condition")).toMatch(/^[A-Za-z0-9_]+$/);
  });

  it("missingRequiredKeys 与服务端必填配置对齐(presence 语义)", () => {
    expect(missingRequiredKeys("sql", {})).toEqual(["sql"]);
    expect(missingRequiredKeys("sql", { sql: "" })).toEqual([]);
    expect(missingRequiredKeys("loop", { items_ref: "a" })).toEqual(["body", "max_iterations"]);
    expect(missingRequiredKeys("condition", { left: "1", op: "==", right: "2" })).toEqual([]);
  });
});
