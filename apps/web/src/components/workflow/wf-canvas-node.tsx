/* 工作流画布自定义节点(107):类型图标 + id + 配置完整度/校验错误定位。
   样式全走 index.css 语义 token(RF 容器继承 html.dark 暗色变量)。 */

import { memo } from "react";
import { CircleAlert } from "lucide-react";
import { Handle, Position, type NodeProps } from "@xyflow/react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { missingRequiredKeys, NODE_TYPE_LABELS } from "@/lib/workflow-canvas";
import { NODE_TYPE_ICONS, type WfFlowNode } from "./workflow-canvas-types";

const ERROR_TEXT_MAX = 60;

function WfCanvasNodeImpl({ id, data, selected }: NodeProps<WfFlowNode>) {
  const label = NODE_TYPE_LABELS[data.nodeType];
  const Icon = NODE_TYPE_ICONS[data.nodeType];
  const missing = missingRequiredKeys(data.nodeType, data.config);
  return (
    <div
      className={cn(
        "min-w-36 max-w-48 rounded-md border bg-card px-3 py-2 shadow-sm transition-shadow",
        selected && "border-ring ring-2 ring-ring/40",
        data.errorText && "border-destructive ring-2 ring-destructive/40",
      )}
      title={data.errorText}
    >
      <Handle type="target" position={Position.Left} className="!size-2.5" />
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0 text-primary" aria-hidden />
        <span className="text-3xs font-medium tracking-wide text-muted-foreground">{label}</span>
        {missing.length > 0 && (
          <Badge variant="destructive" className="ml-auto px-1 py-0 text-3xs">
            缺配置
          </Badge>
        )}
      </div>
      <div className="mt-0.5 truncate font-mono text-xs">{id}</div>
      {data.errorText && (
        <div className="mt-1 flex items-start gap-1 text-2xs text-destructive">
          <CircleAlert className="mt-0.5 size-3 shrink-0" aria-hidden />
          <span className="line-clamp-2">{data.errorText.slice(0, ERROR_TEXT_MAX)}</span>
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!size-2.5" />
    </div>
  );
}

export const WfCanvasNode = memo(WfCanvasNodeImpl);
