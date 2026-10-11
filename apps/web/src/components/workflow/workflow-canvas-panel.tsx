/* 工作流画布面板(107 task 2.1/2.2):左侧节点面板(六类拖拽或点击放置)、
   中央 React Flow 画布(连线表达 depends_on、Shift 框选、滚轮/控件缩放)、
   右侧节点配置侧栏。condition 出边强制 on 二选一(取消即不出线,无 on 值
   条件后继在画布侧不可产生);布局坐标按 wf_key+version 存 localStorage,
   不进定义契约。 */

import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";

// RF 官方样式(仅随本懒加载分包进入;暗色经祖先 html.dark 的 .dark 选择器生效)
import "@xyflow/react/dist/style.css";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import {
  canvasToDag,
  dagToCanvas,
  edgeId,
  loadCanvasPositions,
  NODE_ID_RE,
  NODE_TYPE_LABELS,
  NODE_TYPES,
  nextNodeId,
  RESERVED_NODE_IDS,
  saveCanvasPositions,
  WF_NODE_MIME,
  type CanvasPosition,
  type WfNodeType,
} from "@/lib/workflow-canvas";
import { locateValidationError } from "@/lib/workflow-error-locate";
import { ConditionOnDialog, type PendingConditionEdge } from "./condition-on-dialog";
import { NodeConfigForm } from "./node-config-form";
import { WfCanvasEdge } from "./wf-canvas-edge";
import { WfCanvasNode } from "./wf-canvas-node";
import { NODE_TYPE_ICONS, type WfEdge, type WfFlowNode } from "./workflow-canvas-types";

const nodeTypes = { wf: WfCanvasNode };
const edgeTypes = { wf: WfCanvasEdge };

export interface WorkflowCanvasPanelProps {
  /** 画布投影源(已过可表达性判定的定义对象) */
  definition: unknown;
  /** 外部重投影令牌:初始挂载或 JSON 态切回画布时自增,画布据此重建 */
  initToken: number;
  wfKey: string;
  version: number;
  /** 服务端校验错误文案(空串=无错误;非空时解析定位高亮节点) */
  errorText: string;
  onDefinitionChange: (definition: unknown) => void;
}

function CanvasInner({
  definition,
  initToken,
  wfKey,
  version,
  errorText,
  onDefinitionChange,
}: WorkflowCanvasPanelProps) {
  const [nodes, setNodes] = useNodesState<WfFlowNode>([]);
  const [edges, setEdges] = useEdgesState<WfEdge>([]);
  const { screenToFlowPosition } = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingConditionEdge | null>(null);
  const [fallbackReasons, setFallbackReasons] = useState<string[]>([]);

  // latest-ref:初始化/持久化只应跟随 initToken,不随引用变化重建画布
  const definitionRef = useRef(definition);
  definitionRef.current = definition;
  const wfKeyRef = useRef(wfKey);
  wfKeyRef.current = wfKey;
  const versionRef = useRef(version);
  versionRef.current = version;
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const emitRef = useRef(onDefinitionChange);
  emitRef.current = onDefinitionChange;

  /** 画布态 → 定义并上报(单一数据源:编辑态只有一份定义对象) */
  const emitFrom = useCallback((nextNodes: WfFlowNode[], nextEdges: WfEdge[]) => {
    const back = canvasToDag({
      nodes: nextNodes.map((n) => ({
        id: n.id,
        type: n.data.nodeType,
        config: n.data.config,
        position: n.position,
        depsExplicit: n.data.depsExplicit,
      })),
      edges: nextEdges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        on: e.data?.on ?? null,
      })),
    });
    if (back.ok) emitRef.current(back.definition);
    // !ok 属防御分支(正常交互不会产出冲突图),保持上次定义不变
  }, []);

  /** 图结构变更即作废旧的服务端错误定位(新编辑使旧判定过期) */
  const stripErrors = useCallback(
    (ns: WfFlowNode[]): WfFlowNode[] =>
      ns.some((n) => n.data.errorText !== undefined)
        ? ns.map((n) => ({ ...n, data: { ...n.data, errorText: undefined } }))
        : ns,
    [],
  );

  const persistPositions = useCallback((ns: WfFlowNode[]) => {
    const map: Record<string, CanvasPosition> = {};
    for (const n of ns) map[n.id] = n.position;
    saveCanvasPositions(wfKeyRef.current, versionRef.current, map);
  }, []);

  /** 拖拽结束持久化全量布局(整表写,不丢未拖动节点的坐标) */
  const handleNodeDragStop = useCallback(() => {
    persistPositions(nodesRef.current);
  }, [persistPositions]);

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
    setNodes(
      to.graph.nodes.map((cnode) => ({
        id: cnode.id,
        type: "wf" as const,
        position: cnode.position,
        data: {
          nodeType: cnode.type,
          config: cnode.config,
          depsExplicit: cnode.depsExplicit,
        },
      })),
    );
    setEdges(
      to.graph.edges.map((ce) => ({
        id: ce.id,
        source: ce.source,
        target: ce.target,
        type: "wf" as const,
        data: { on: ce.on },
      })),
    );
  }, [initToken, setNodes, setEdges]);

  // 服务端校验错误 → 节点高亮定位(环参与节点/非法 on/缺配置;design 裁决 4)
  useEffect(() => {
    if (errorText === "") {
      setNodes((ns) => (ns.some((n) => n.data.errorText !== undefined) ? stripErrors(ns) : ns));
      return;
    }
    const { nodeIds } = locateValidationError(errorText);
    if (nodeIds.length === 0) return;
    const hit = new Set(nodeIds);
    // 未命中节点一并清除旧 errorText(评审修复:两次不同错误的定位间,
    // 残留高亮会误导排障;侧栏派生与画布高亮保持同一生命周期)
    setNodes((ns) =>
      ns.map((n) => {
        if (hit.has(n.id)) return { ...n, data: { ...n.data, errorText } };
        return n.data.errorText !== undefined
          ? { ...n, data: { ...n.data, errorText: undefined } }
          : n;
      }),
    );
  }, [errorText, setNodes, stripErrors]);

  const handleNodesChange = useCallback(
    (changes: NodeChange<WfFlowNode>[]) => {
      const nextNodes = applyNodeChanges(changes, nodes);
      setNodes(nextNodes);
      if (!changes.some((c) => c.type === "remove")) return;
      const alive = new Set(nextNodes.map((n) => n.id));
      const nextEdges = edges.filter((e) => alive.has(e.source) && alive.has(e.target));
      if (nextEdges.length !== edges.length) setEdges(nextEdges);
      setSelectedId((s) => (s !== null && alive.has(s) ? s : null));
      emitFrom(stripErrors(nextNodes), nextEdges);
    },
    [nodes, edges, setNodes, setEdges, emitFrom, stripErrors],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<WfEdge>[]) => {
      const nextEdges = applyEdgeChanges(changes, edges);
      setEdges(nextEdges);
      if (changes.some((c) => c.type === "remove")) emitFrom(nodes, nextEdges);
    },
    [nodes, edges, setEdges, emitFrom],
  );

  const isConditionSource = useCallback(
    (source: string) => nodes.find((n) => n.id === source)?.data.nodeType === "condition",
    [nodes],
  );

  /** 目标节点现存的 on 值(同目标条件入边共用;无则 null) */
  const existingOnOf = useCallback(
    (target: string): "true" | "false" | null =>
      edges.find((e) => e.target === target && isConditionSource(e.source))?.data?.on ?? null,
    [edges, isConditionSource],
  );

  const addEdgeWith = useCallback(
    (source: string, target: string, on: "true" | "false" | null) => {
      const nextEdges: WfEdge[] = [
        ...edges.filter((e) => !(e.source === source && e.target === target)),
        { id: edgeId(source, target), source, target, type: "wf" as const, data: { on } },
      ];
      setEdges(nextEdges);
      emitFrom(stripErrors(nodes), nextEdges);
    },
    [edges, nodes, setEdges, emitFrom, stripErrors],
  );

  const handleConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target || conn.source === conn.target) return;
      if (edges.some((e) => e.source === conn.source && e.target === conn.target)) return;
      // condition 出边必须显式指定 on 值:弹二选一,取消即不出线(spec MUST NOT)
      if (isConditionSource(conn.source)) {
        setPending({
          source: conn.source,
          target: conn.target,
          existingOn: existingOnOf(conn.target),
        });
        return;
      }
      addEdgeWith(conn.source, conn.target, null);
    },
    [edges, isConditionSource, existingOnOf, addEdgeWith],
  );

  /** on 值确认:新边 + 同目标既有条件入边同步为同一 on(节点级单一语义) */
  const confirmConditionOn = useCallback(
    (on: "true" | "false") => {
      if (!pending) return;
      const { source, target } = pending;
      const nextEdges: WfEdge[] = edges.map((e) =>
        e.target === target && isConditionSource(e.source) ? { ...e, data: { ...e.data, on } } : e,
      );
      if (!nextEdges.some((e) => e.source === source && e.target === target))
        nextEdges.push({
          id: edgeId(source, target),
          source,
          target,
          type: "wf" as const,
          data: { on },
        });
      setEdges(nextEdges);
      setPending(null);
      emitFrom(stripErrors(nodes), nextEdges);
    },
    [pending, edges, nodes, isConditionSource, setEdges, emitFrom, stripErrors],
  );

  const setConditionOn = useCallback(
    (targetId: string, on: "true" | "false") => {
      const nextEdges: WfEdge[] = edges.map((e) =>
        e.target === targetId && isConditionSource(e.source)
          ? { ...e, data: { ...e.data, on } }
          : e,
      );
      setEdges(nextEdges);
      emitFrom(stripErrors(nodes), nextEdges);
    },
    [edges, nodes, isConditionSource, setEdges, emitFrom, stripErrors],
  );

  const addNode = useCallback(
    (nodeType: WfNodeType, position: CanvasPosition) => {
      const nextNodes: WfFlowNode[] = [
        ...nodes,
        {
          id: nextNodeId(nodes, nodeType),
          type: "wf" as const,
          position,
          data: { nodeType, config: {}, depsExplicit: false },
        },
      ];
      setNodes(nextNodes);
      persistPositions(nextNodes);
      // 结构变更即回写定义(单一数据源;评审修复:缺 emit 时新节点只存在于
      // 画布投影,保存取 definition 会静默丢失该节点)
      emitFrom(stripErrors(nextNodes), edges);
    },
    [nodes, edges, setNodes, persistPositions, emitFrom, stripErrors],
  );

  const addNodeAtCenter = useCallback(
    (nodeType: WfNodeType) => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      const position = rect
        ? screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 3 })
        : { x: 120, y: 120 };
      addNode(nodeType, position);
    },
    [screenToFlowPosition, addNode],
  );

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const nodeType = event.dataTransfer.getData(WF_NODE_MIME);
      if (!(NODE_TYPES as readonly string[]).includes(nodeType)) return;
      addNode(nodeType as WfNodeType, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
    },
    [screenToFlowPosition, addNode],
  );

  const updateConfig = useCallback(
    (id: string, config: Record<string, unknown>) => {
      const nextNodes = nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, config, errorText: undefined } } : n,
      );
      setNodes(nextNodes);
      emitFrom(nextNodes, edges);
    },
    [nodes, edges, setNodes, emitFrom],
  );

  const renameNode = useCallback(
    (oldId: string, newId: string) => {
      if (
        oldId === newId ||
        !NODE_ID_RE.test(newId) ||
        RESERVED_NODE_IDS.has(newId) ||
        nodes.some((n) => n.id === newId)
      )
        return;
      const nextNodes = nodes.map((n) =>
        n.id === oldId ? { ...n, id: newId, data: { ...n.data, errorText: undefined } } : n,
      );
      const nextEdges = edges.map((e) => ({
        ...e,
        id: edgeId(e.source === oldId ? newId : e.source, e.target === oldId ? newId : e.target),
        source: e.source === oldId ? newId : e.source,
        target: e.target === oldId ? newId : e.target,
      }));
      setNodes(nextNodes);
      setEdges(nextEdges);
      setSelectedId(newId);
      emitFrom(nextNodes, nextEdges);
    },
    [nodes, edges, setNodes, setEdges, emitFrom],
  );

  const deleteNode = useCallback(
    (id: string) => {
      const nextNodes = nodes.filter((n) => n.id !== id);
      const nextEdges = edges.filter((e) => e.source !== id && e.target !== id);
      setNodes(nextNodes);
      setEdges(nextEdges);
      setSelectedId((s) => (s === id ? null : s));
      emitFrom(nextNodes, nextEdges);
    },
    [nodes, edges, setNodes, setEdges, emitFrom],
  );

  const selectedNode = nodes.find((n) => n.id === selectedId) ?? null;
  const located = errorText === "" ? null : locateValidationError(errorText);
  const nodeServerError =
    selectedNode && located?.nodeIds.includes(selectedNode.id)
      ? { message: located.message, configKey: located.configKey }
      : null;

  return (
    <div className="flex flex-col md:h-[60vh] md:flex-row">
      {/* 节点面板:拖拽到画布或点击居中放置 */}
      <div className="flex shrink-0 gap-2 overflow-x-auto border-b p-2 md:w-36 md:flex-col md:overflow-y-auto md:border-b-0 md:border-r">
        <p className="hidden px-1 text-3xs font-medium text-muted-foreground md:block">节点类型</p>
        <p className="px-1 text-3xs text-muted-foreground md:hidden">拖到画布或点击放置:</p>
        {NODE_TYPES.map((t) => {
          const Icon = NODE_TYPE_ICONS[t];
          return (
            <Button
              key={t}
              type="button"
              variant="outline"
              draggable
              className="shrink-0 justify-start gap-1.5 px-2 md:w-full"
              onDragStart={(e) => {
                e.dataTransfer.setData(WF_NODE_MIME, t);
                e.dataTransfer.effectAllowed = "move";
              }}
              onClick={() => addNodeAtCenter(t)}
            >
              <Icon className="size-3.5 shrink-0 text-primary" aria-hidden />
              <span className="text-xs">{NODE_TYPE_LABELS[t]}</span>
            </Button>
          );
        })}
      </div>

      {/* 画布区 */}
      <div
        ref={wrapperRef}
        className="relative h-72 md:h-auto md:flex-1"
        onDrop={handleDrop}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
        }}
      >
        <ReactFlow<WfFlowNode, WfEdge>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          defaultEdgeOptions={{ type: "wf" }}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onConnect={handleConnect}
          onNodeDragStop={handleNodeDragStop}
          onSelectionChange={(sel) => setSelectedId(sel.nodes[0]?.id ?? null)}
          onPaneClick={() => setSelectedId(null)}
          deleteKeyCode={["Backspace", "Delete"]}
          minZoom={0.2}
          maxZoom={1.5}
          fitView
        >
          <Background variant={BackgroundVariant.Dots} gap={16} />
          <Controls showInteractive={false} />
        </ReactFlow>
        {fallbackReasons.length > 0 && (
          <div
            role="alert"
            className="absolute inset-x-4 top-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"
          >
            画布无法表达当前定义:{fallbackReasons.join(";")}
          </div>
        )}
        {nodes.length === 0 && fallbackReasons.length === 0 && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-4">
            <p className="max-w-60 text-center text-xs text-muted-foreground">
              从左侧拖入节点类型开始编排;节点间拖线表达依赖。
            </p>
          </div>
        )}
      </div>

      {/* 节点配置侧栏 */}
      <aside className="w-full shrink-0 border-t p-3 md:w-80 md:overflow-y-auto md:border-l md:border-t-0 md:p-3">
        {selectedNode ? (
          <NodeConfigForm
            key={selectedNode.id}
            nodeId={selectedNode.id}
            nodeType={selectedNode.data.nodeType}
            config={selectedNode.data.config}
            conditionOn={existingOnOf(selectedNode.id)}
            serverError={nodeServerError}
            takenIds={nodes.map((n) => n.id)}
            onConfigChange={(config) => updateConfig(selectedNode.id, config)}
            onIdChange={(newId) => renameNode(selectedNode.id, newId)}
            onOnValueChange={(on) => setConditionOn(selectedNode.id, on)}
            onDelete={() => deleteNode(selectedNode.id)}
          />
        ) : (
          <EmptyState>
            {nodes.length === 0
              ? "从左侧拖入节点类型开始编排。"
              : "点选节点编辑配置;从节点右缘拖线连接依赖。"}
          </EmptyState>
        )}
      </aside>

      <ConditionOnDialog
        pending={pending}
        onConfirm={confirmConditionOn}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}

export function WorkflowCanvasPanel(props: WorkflowCanvasPanelProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}
