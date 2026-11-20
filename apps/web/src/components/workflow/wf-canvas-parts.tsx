/* 工作流画布的周边区块：节点类型面板、拖放处理、选中节点配置侧栏。 */

import type { DragEvent } from "react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { NODE_TYPE_LABELS, NODE_TYPES, WF_NODE_MIME, type WfNodeType } from "@/lib/workflow-canvas";
import { NodeConfigForm } from "./node-config-form";
import { NODE_TYPE_ICONS, type WfFlowNode } from "./workflow-canvas-types";

/* 阻止默认行为并提示为移动，允许画布接收节点类型拖放。 */
export function handleDragOver(event: DragEvent<HTMLDivElement>): void {
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
}

/* 节点面板支持拖拽或点击放置；nokey 阻止面板按键触发画布删除。 */
export function NodePalette({ onPlace }: { onPlace: (t: WfNodeType) => void }) {
  return (
    <div className="nokey flex shrink-0 gap-2 overflow-x-auto border-b p-2 md:w-36 md:flex-col md:overflow-y-auto md:border-b-0 md:border-r">
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
            onClick={() => onPlace(t)}
          >
            <Icon className="size-3.5 shrink-0 text-primary" aria-hidden />
            <span className="text-xs">{NODE_TYPE_LABELS[t]}</span>
          </Button>
        );
      })}
    </div>
  );
}

/* nokey 排除侧栏控件的按键事件,避免 Backspace 删除画布选中节点。 */
export function CanvasSidePanel({
  selectedNode,
  nodeServerError,
  nodes,
  existingOnOf,
  onConfigChange,
  onRename,
  onConditionOn,
  onDelete,
}: {
  selectedNode: WfFlowNode | null;
  nodeServerError: { message: string; configKey?: string | undefined } | null;
  nodes: WfFlowNode[];
  existingOnOf: (target: string) => "true" | "false" | null;
  onConfigChange: (id: string, config: Record<string, unknown>) => void;
  onRename: (oldId: string, newId: string) => void;
  onConditionOn: (id: string, on: "true" | "false") => void;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="nokey w-full shrink-0 border-t p-3 md:w-80 md:overflow-y-auto md:border-l md:border-t-0 md:p-3">
      {selectedNode ? (
        <NodeConfigForm
          key={selectedNode.id}
          nodeId={selectedNode.id}
          nodeType={selectedNode.data.nodeType}
          config={selectedNode.data.config}
          conditionOn={existingOnOf(selectedNode.id)}
          serverError={nodeServerError}
          takenIds={nodes.map((n) => n.id)}
          onConfigChange={(config) => onConfigChange(selectedNode.id, config)}
          onIdChange={(newId) => onRename(selectedNode.id, newId)}
          onOnValueChange={(on) => onConditionOn(selectedNode.id, on)}
          onDelete={() => onDelete(selectedNode.id)}
        />
      ) : (
        <EmptyState>
          {nodes.length === 0
            ? "从左侧拖入节点类型开始编排。"
            : "点选节点编辑配置;从节点右缘拖线连接依赖。"}
        </EmptyState>
      )}
    </aside>
  );
}
