/* 画布图结构的纯函数：定义/画布互转、错误标注、条件入边判定与边维护。 */

import {
  canvasToDag,
  edgeId,
  NODE_ID_RE,
  RESERVED_NODE_IDS,
  type WfNodeType,
} from "@kongming/api-client/workflow-canvas";
import { locateValidationError } from "@kongming/api-client/workflow-error-locate";
import type { WfEdge, WfFlowNode } from "./workflow-canvas-types";

/* dagToCanvas 产出的节点视图（画布坐标由 dagToCanvas 叠加本地布局后给出）。 */
type CanvasNodeView = {
  id: string;
  type: WfNodeType;
  position: { x: number; y: number };
  config: Record<string, unknown>;
  depsExplicit: boolean;
};

type CanvasEdgeView = {
  id: string;
  source: string;
  target: string;
  on: "true" | "false" | null;
};

/* 定义侧节点视图 → React Flow 节点（渲染类型恒为 "wf"）。 */
export function toFlowNode(cnode: CanvasNodeView): WfFlowNode {
  return {
    id: cnode.id,
    type: "wf" as const,
    position: cnode.position,
    data: {
      nodeType: cnode.type,
      config: cnode.config,
      depsExplicit: cnode.depsExplicit,
    },
  };
}

/* 定义侧边视图 → React Flow 边（渲染类型恒为 "wf"）。 */
export function toFlowEdge(ce: CanvasEdgeView): WfEdge {
  return {
    id: ce.id,
    source: ce.source,
    target: ce.target,
    type: "wf" as const,
    data: { on: ce.on },
  };
}

/* 画布态 → 定义并上报用的面板回调签名；!ok 属防御分支(正常交互不会产出冲突图),保持上次定义不变。 */
export function canvasToDefinition(nodes: WfFlowNode[], edges: WfEdge[]) {
  return canvasToDag({
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.nodeType,
      config: n.data.config,
      position: n.position,
      depsExplicit: n.data.depsExplicit,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      on: e.data?.on ?? null,
    })),
  });
}

/* 改名可行性：与现值不同、符合 id 字符集、避开保留名与其他节点。 */
export function newIdAcceptable(
  nodes: WfFlowNode[],
  oldId: string,
  newId: string,
): boolean {
  if (oldId === newId) return false;
  if (!NODE_ID_RE.test(newId)) return false;
  if (RESERVED_NODE_IDS.has(newId)) return false;
  return !nodes.some((n) => n.id === newId);
}

/** 图结构变更即作废旧的服务端错误定位(新编辑使旧判定过期) */
export function stripErrors(ns: WfFlowNode[]): WfFlowNode[] {
  if (!ns.some((n) => n.data.errorText !== undefined)) return ns;
  return ns.map((n) => ({ ...n, data: { ...n.data, errorText: undefined } }));
}

/* 把错误定位结果写回节点标注：命中节点写入文案，其余节点清掉旧标注。 */
export function applyErrorHighlight(
  ns: WfFlowNode[],
  errorText: string,
): WfFlowNode[] {
  const { nodeIds } = locateValidationError(errorText);
  if (nodeIds.length === 0) return ns;
  const hit = new Set(nodeIds);
  // 清除未命中节点的旧错误，避免画布高亮与侧栏状态不一致。
  return ns.map((n) => {
    if (hit.has(n.id)) return { ...n, data: { ...n.data, errorText } };
    return n.data.errorText !== undefined
      ? { ...n, data: { ...n.data, errorText: undefined } }
      : n;
  });
}

/* 最近一次错误定位命中的本节点错误；未选中或未命中返回 null。 */
export function locatedServerError(
  selected: WfFlowNode | null,
  errorText: string,
): { message: string; configKey?: string | undefined } | null {
  if (!selected || errorText === "") return null;
  const located = locateValidationError(errorText);
  return located.nodeIds.includes(selected.id)
    ? { message: located.message, configKey: located.configKey }
    : null;
}

/* 判断边源是否为条件节点（决定出边是否必须先选 on 值）。 */
export function isConditionSourceOf(
  nodes: WfFlowNode[],
  source: string,
): boolean {
  return nodes.find((n) => n.id === source)?.data.nodeType === "condition";
}

/** 目标节点现存的 on 值(同目标条件入边共用;无则 null) */
export function existingConditionOn(
  edges: WfEdge[],
  nodes: WfFlowNode[],
  target: string,
): "true" | "false" | null {
  return (
    edges.find(
      (e) => e.target === target && isConditionSourceOf(nodes, e.source),
    )?.data?.on ?? null
  );
}

/* 同源同目标的旧边先移除再追加新边，保证一对节点间只有一条依赖。 */
export function upsertEdge(
  edges: WfEdge[],
  edge: { source: string; target: string; on: "true" | "false" | null },
): WfEdge[] {
  const { source, target, on } = edge;
  return [
    ...edges.filter((e) => !(e.source === source && e.target === target)),
    {
      id: edgeId(source, target),
      source,
      target,
      type: "wf" as const,
      data: { on },
    },
  ];
}

/* 把指向某节点的所有条件入边同步为同一 on（节点级单一语义）。 */
export function rewriteConditionOn(
  edges: WfEdge[],
  cond: { nodes: WfFlowNode[]; target: string; on: "true" | "false" },
): WfEdge[] {
  const { nodes, target, on } = cond;
  return edges.map((e) =>
    e.target === target && isConditionSourceOf(nodes, e.source)
      ? { ...e, data: { ...e.data, on } }
      : e,
  );
}

/* 节点改名后，入/出边两端换成新 id 并重算边 id。 */
export function renameNodeEdges(
  edges: WfEdge[],
  oldId: string,
  newId: string,
): WfEdge[] {
  const map = (id: string): string => (id === oldId ? newId : id);
  return edges.map((e) => ({
    ...e,
    id: edgeId(map(e.source), map(e.target)),
    source: map(e.source),
    target: map(e.target),
  }));
}

/* 删除节点后去掉其两端悬空边。 */
export function dropNodeEdges(edges: WfEdge[], id: string): WfEdge[] {
  return edges.filter((e) => e.source !== id && e.target !== id);
}

/* 新建节点的初始数据：空配置、未显式声明依赖。 */
export function newNode(
  nodeType: WfNodeType,
  id: string,
  position: { x: number; y: number },
): WfFlowNode {
  return {
    id,
    type: "wf" as const,
    position,
    data: { nodeType, config: {}, depsExplicit: false },
  };
}
