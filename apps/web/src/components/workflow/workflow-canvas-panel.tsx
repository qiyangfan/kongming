/* 工作流画布提供节点放置、依赖连线和配置侧栏。布局只存于本地；condition 出边需明确选择 on，取消时不创建连线。
   latest-ref 约定：初始化/持久化只跟随 initToken，回调经 ref 读取最新的定义、键、版本与节点列表。 */

import { useCallback, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";

// RF 官方样式(仅随本懒加载分包进入;暗色经祖先 html.dark 的 .dark 选择器生效)
import "@xyflow/react/dist/style.css";

import { ConditionOnDialog, type PendingConditionEdge } from "./condition-on-dialog";
import {
  useCanvasDrop,
  useCanvasEdges,
  useCanvasNodes,
  useCanvasEmit,
  useCanvasSync,
  useLatestRef,
} from "./use-workflow-canvas";
import { WfCanvasEdge } from "./wf-canvas-edge";
import { WfCanvasNode } from "./wf-canvas-node";
import { CanvasSidePanel, handleDragOver, NodePalette } from "./wf-canvas-parts";
import { locatedServerError } from "./wf-graph-helpers";
import type { WfEdge, WfFlowNode } from "./workflow-canvas-types";

const nodeTypes = { wf: WfCanvasNode };
const edgeTypes = { wf: WfCanvasEdge };

interface WorkflowCanvasPanelProps {
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
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingConditionEdge | null>(null);
  // 待确认连线的最新值：确认回调读取此引用，避免闭包滞后
  const pendingRef = useLatestRef(pending);
  const [fallbackReasons, setFallbackReasons] = useState<string[]>([]);

  // latest-ref:初始化/持久化只应跟随 initToken,不随引用变化重建画布
  const definitionRef = useLatestRef(definition);
  const wfKeyRef = useLatestRef(wfKey);
  const versionRef = useLatestRef(version);
  const nodesRef = useLatestRef(nodes);
  const emitRef = useLatestRef(onDefinitionChange);

  const { emitFrom, persistPositions } = useCanvasEmit({
    wfKeyRef,
    versionRef,
    emitRef,
  });

  /** 拖拽结束持久化全量布局(整表写,不丢未拖动节点的坐标) */
  const handleNodeDragStop = useCallback(() => {
    persistPositions(nodesRef.current);
  }, [persistPositions, nodesRef]);

  // 外部重投影与错误高亮同步
  useCanvasSync({
    initToken,
    errorText,
    definitionRef,
    wfKeyRef,
    versionRef,
    setNodes,
    setEdges,
    setSelectedId,
    setFallbackReasons,
  });

  const { addNode, handleNodesChange, updateConfig, renameNode, deleteNode, addNodeAtCenter } =
    useCanvasNodes({
      nodes,
      edges,
      setNodes,
      setEdges,
      setSelectedId,
      emitFrom,
      persistPositions,
      wrapperRef,
    });
  const { handleEdgesChange, existingOnOf, handleConnect, confirmConditionOn, setConditionOn } =
    useCanvasEdges({
      nodes,
      edges,
      setEdges,
      setPending,
      emitFrom,
      pendingRef,
    });
  const handleDrop = useCanvasDrop({ addNode });

  const selectedNode = nodes.find((n) => n.id === selectedId) ?? null;
  const nodeServerError = locatedServerError(selectedNode, errorText);

  return (
    <div className="flex flex-col md:h-[60vh] md:flex-row">
      <NodePalette onPlace={addNodeAtCenter} />

      <div
        ref={wrapperRef}
        className="relative h-72 md:h-auto md:flex-1"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
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

      <CanvasSidePanel
        selectedNode={selectedNode}
        nodeServerError={nodeServerError}
        nodes={nodes}
        existingOnOf={existingOnOf}
        onConfigChange={updateConfig}
        onRename={renameNode}
        onConditionOn={setConditionOn}
        onDelete={deleteNode}
      />

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
