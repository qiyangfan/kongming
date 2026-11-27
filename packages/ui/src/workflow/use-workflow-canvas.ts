/* 工作流画布的状态同步与交互操作：外部重投影、错误高亮刷新、节点/连线增删改。 */

import {
  useCallback,
  useEffect,
  useRef,
  type DragEvent,
  type RefObject,
} from "react";
import {
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";

import {
  saveCanvasPositions,
  loadCanvasPositions,
  dagToCanvas,
  NODE_TYPES,
  nextNodeId,
  WF_NODE_MIME,
  type CanvasPosition,
  type WfNodeType,
} from "@kongming/api-client/workflow-canvas";
import type { PendingConditionEdge } from "./condition-on-dialog";
import type { WfEdge, WfFlowNode } from "./workflow-canvas-types";
import {
  applyErrorHighlight,
  dropNodeEdges,
  existingConditionOn,
  isConditionSourceOf,
  newIdAcceptable,
  newNode,
  renameNodeEdges,
  rewriteConditionOn,
  stripErrors,
  toFlowEdge,
  toFlowNode,
  canvasToDefinition,
  upsertEdge,
} from "./wf-graph-helpers";

type SetNodes = ReturnType<
  typeof import("@xyflow/react").useNodesState<WfFlowNode>
>[1];
type SetEdges = ReturnType<
  typeof import("@xyflow/react").useEdgesState<WfEdge>
>[1];
type SetSelectedId = (
  value: string | null | ((s: string | null) => string | null),
) => void;

/* 随渲染刷新的最新值引用：回调经 ref 读取，不因引用变化重建。 */
export function useLatestRef<T>(value: T): RefObject<T> {
  const ref = useRef(value);
  ref.current = value;
  return ref;
}

export function useCanvasSync({
  initToken,
  errorText,
  definitionRef,
  wfKeyRef,
  versionRef,
  setNodes,
  setEdges,
  setSelectedId,
  setFallbackReasons,
}: {
  initToken: number;
  errorText: string;
  definitionRef: RefObject<unknown>;
  wfKeyRef: RefObject<string>;
  versionRef: RefObject<number>;
  setNodes: SetNodes;
  setEdges: SetEdges;
  setSelectedId: SetSelectedId;
  setFallbackReasons: (v: string[]) => void;
}) {
  // 外部重投影(初始挂载 / JSON 态切回):定义 → 画布态(坐标叠加 localStorage 布局)
  useEffect(() => {
    const to = dagToCanvas(
      definitionRef.current,
      loadCanvasPositions(wfKeyRef.current, versionRef.current),
    );
    setSelectedId(null);
    if (!to.ok) {
      // 防御:进入画布前编辑器已判定可表达;此处兜底展示原因
      setFallbackReasons(to.reasons);
      setNodes([]);
      setEdges([]);
      return;
    }
    setFallbackReasons([]);
    setNodes(to.graph.nodes.map(toFlowNode));
    setEdges(to.graph.edges.map(toFlowEdge));
  }, [
    initToken,
    setNodes,
    setEdges,
    definitionRef,
    wfKeyRef,
    versionRef,
    setSelectedId,
    setFallbackReasons,
  ]);

  // 重投影(initToken 变化)会重建节点并丢掉错误标注,故随 errorText 重新应用定位高亮。
  useEffect(() => {
    if (errorText === "") {
      setNodes((ns) => stripErrors(ns));
      return;
    }
    setNodes((ns) => applyErrorHighlight(ns, errorText));
  }, [errorText, initToken, setNodes]);
}

/* 画布交互公共签名：节点/边状态、选择与待确认连线及其写入函数。 */
type CanvasStateArgs = {
  nodes: WfFlowNode[];
  edges: WfEdge[];
  setNodes: SetNodes;
  setEdges: SetEdges;
  setSelectedId: SetSelectedId;
  setPending: (v: PendingConditionEdge | null) => void;
  /** 待确认连线的最新值（供确认回调读取，避免闭包滞后） */
  pendingRef: RefObject<PendingConditionEdge | null>;
  emitFrom: (ns: WfFlowNode[], es: WfEdge[]) => void;
  persistPositions: (ns: WfFlowNode[]) => void;
};

/* 改边后统一写入并回写定义（图结构变更即作废旧的服务端错误定位）。 */
function useCommitEdges({
  nodes,
  setEdges,
  emitFrom,
}: Pick<CanvasStateArgs, "nodes" | "setEdges" | "emitFrom">) {
  return useCallback(
    (nextEdges: WfEdge[]) => {
      setEdges(nextEdges);
      emitFrom(stripErrors(nodes), nextEdges);
    },
    [nodes, setEdges, emitFrom],
  );
}

/* 节点选择、增删、改配置与改名。 */
export function useCanvasNodes({
  nodes,
  edges,
  setNodes,
  setEdges,
  setSelectedId,
  emitFrom,
  persistPositions,
  wrapperRef,
}: Omit<CanvasStateArgs, "setPending" | "pendingRef"> & {
  wrapperRef: RefObject<HTMLDivElement | null>;
}) {
  /* 新增节点：按类型取下一个可用 id，落盘布局并立即回写定义（保存时含新节点）。 */
  const addNode = useCallback(
    (nodeType: WfNodeType, position: CanvasPosition) => {
      const nextNodes: WfFlowNode[] = [
        ...nodes,
        newNode(nodeType, nextNodeId(nodes, nodeType), position),
      ];
      setNodes(nextNodes);
      persistPositions(nextNodes);
      emitFrom(stripErrors(nextNodes), edges);
    },
    [nodes, edges, setNodes, persistPositions, emitFrom],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange<WfFlowNode>[]) => {
      const nextNodes = applyNodeChanges(changes, nodes);
      setNodes(nextNodes);
      if (!changes.some((c) => c.type === "remove")) return;
      const alive = new Set(nextNodes.map((n) => n.id));
      const nextEdges = edges.filter(
        (e) => alive.has(e.source) && alive.has(e.target),
      );
      if (nextEdges.length !== edges.length) setEdges(nextEdges);
      setSelectedId((s) => (s !== null && alive.has(s) ? s : null));
      emitFrom(stripErrors(nextNodes), nextEdges);
    },
    [nodes, edges, setNodes, setEdges, setSelectedId, emitFrom],
  );

  const updateConfig = useCallback(
    (id: string, config: Record<string, unknown>) => {
      const nextNodes = nodes.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config, errorText: undefined } }
          : n,
      );
      setNodes(nextNodes);
      emitFrom(nextNodes, edges);
    },
    [nodes, edges, setNodes, emitFrom],
  );

  /* 改名：本地校验通过才改节点 id 与两端边；坐标按新 id 重存。 */
  const renameNode = useCallback(
    (oldId: string, newId: string) => {
      if (!newIdAcceptable(nodes, oldId, newId)) return;
      const nextNodes = nodes.map((n) =>
        n.id === oldId
          ? { ...n, id: newId, data: { ...n.data, errorText: undefined } }
          : n,
      );
      const nextEdges = renameNodeEdges(edges, oldId, newId);
      setNodes(nextNodes);
      persistPositions(nextNodes); // 节点改名后按新 id 保存坐标。
      setEdges(nextEdges);
      setSelectedId(newId);
      emitFrom(nextNodes, nextEdges);
    },
    [
      nodes,
      edges,
      setNodes,
      setEdges,
      setSelectedId,
      persistPositions,
      emitFrom,
    ],
  );

  const deleteNode = useCallback(
    (id: string) => {
      const nextNodes = nodes.filter((n) => n.id !== id);
      const nextEdges = dropNodeEdges(edges, id);
      setNodes(nextNodes);
      setEdges(nextEdges);
      setSelectedId((s) => (s === id ? null : s));
      emitFrom(nextNodes, nextEdges);
    },
    [nodes, edges, setNodes, setEdges, setSelectedId, emitFrom],
  );

  /* 点击面板按钮时放到画布可视区中部偏上；无容器尺寸时落到固定坐标。 */
  const screenToFlowPosition = useReactFlow().screenToFlowPosition;
  /* 点击面板按钮时放到画布可视区中部偏上；无容器尺寸时落到固定坐标。 */
  const addNodeAtCenter = useCallback(
    (nodeType: WfNodeType) => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      const position = rect
        ? screenToFlowPosition({
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 3,
          })
        : { x: 120, y: 120 };
      addNode(nodeType, position);
    },
    [screenToFlowPosition, wrapperRef, addNode],
  );

  return {
    addNode,
    handleNodesChange,
    updateConfig,
    renameNode,
    deleteNode,
    addNodeAtCenter,
  };
}

/* 连线、条件 on 同步与边删除。 */
export function useCanvasEdges({
  nodes,
  edges,
  setEdges,
  setPending,
  emitFrom,
  pendingRef,
}: Pick<
  CanvasStateArgs,
  "nodes" | "edges" | "setEdges" | "setPending" | "emitFrom" | "pendingRef"
>) {
  const commitEdges = useCommitEdges({ nodes, setEdges, emitFrom });
  const pending = pendingRef;

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<WfEdge>[]) => {
      const nextEdges = applyEdgeChanges(changes, edges);
      setEdges(nextEdges);
      if (changes.some((c) => c.type === "remove")) emitFrom(nodes, nextEdges);
    },
    [nodes, edges, setEdges, emitFrom],
  );

  const existingOnOf = useCallback(
    (target: string): "true" | "false" | null =>
      existingConditionOn(edges, nodes, target),
    [edges, nodes],
  );

  /* 连线：同源同目标不重复；条件源先弹窗选 on，取消则不建边。 */
  const handleConnect = useCallback(
    (conn: Connection) => {
      const { source, target } = conn;
      if (!source || !target || source === target) return;
      if (edges.some((e) => e.source === source && e.target === target)) return;
      if (isConditionSourceOf(nodes, source)) {
        setPending({ source, target, existingOn: existingOnOf(target) });
        return;
      }
      commitEdges(upsertEdge(edges, { source, target, on: null }));
    },
    [edges, nodes, existingOnOf, setPending, commitEdges],
  );

  /** on 值确认:新边 + 同目标既有条件入边同步为同一 on(节点级单一语义) */
  const confirmConditionOn = useCallback(
    (on: "true" | "false") => {
      const cur = pending.current;
      if (!cur) return;
      const synced = rewriteConditionOn(edges, {
        nodes,
        target: cur.target,
        on,
      });
      const exists = synced.some(
        (e) => e.source === cur.source && e.target === cur.target,
      );
      commitEdges(
        exists
          ? synced
          : upsertEdge(synced, { source: cur.source, target: cur.target, on }),
      );
      setPending(null);
    },
    [edges, nodes, pending, commitEdges, setPending],
  );

  const setConditionOn = useCallback(
    (targetId: string, on: "true" | "false") => {
      commitEdges(rewriteConditionOn(edges, { nodes, target: targetId, on }));
    },
    [edges, nodes, commitEdges],
  );

  return {
    handleEdgesChange,
    existingOnOf,
    handleConnect,
    confirmConditionOn,
    setConditionOn,
  };
}

/* 面板内两条回写回调：画布态 → 定义上报、本地布局持久化。 */
export function useCanvasEmit({
  wfKeyRef,
  versionRef,
  emitRef,
}: {
  wfKeyRef: RefObject<string>;
  versionRef: RefObject<number>;
  emitRef: RefObject<(definition: unknown) => void>;
}) {
  const emitFrom = useCallback(
    (nextNodes: WfFlowNode[], nextEdges: WfEdge[]) => {
      const back = canvasToDefinition(nextNodes, nextEdges);
      if (back.ok) emitRef.current(back.definition);
    },
    [emitRef],
  );
  const persistPositions = useCallback(
    (ns: WfFlowNode[]) => {
      const map: Record<string, CanvasPosition> = {};
      for (const n of ns) map[n.id] = n.position;
      saveCanvasPositions(wfKeyRef.current, versionRef.current, map);
    },
    [wfKeyRef, versionRef],
  );
  return { emitFrom, persistPositions };
}

/* 从节点面板拖入画布：类型不在白名单内则忽略。 */
export function useCanvasDrop({
  addNode,
}: {
  addNode: (t: WfNodeType, p: CanvasPosition) => void;
}) {
  const { screenToFlowPosition } = useReactFlow();
  return useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const nodeType = event.dataTransfer.getData(WF_NODE_MIME);
      if (!(NODE_TYPES as readonly string[]).includes(nodeType)) return;
      addNode(
        nodeType as WfNodeType,
        screenToFlowPosition({ x: event.clientX, y: event.clientY }),
      );
    },
    [screenToFlowPosition, addNode],
  );
}
