/* 工作流画布自定义边(107):普通依赖边素线;条件出边带 on 值徽标
   (true/false 分支边,MUST NOT 出现无 on 值条件后继连线)。 */

import { memo } from "react";
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from "@xyflow/react";

import { Badge } from "@/components/ui/badge";
import type { WfEdge } from "./workflow-canvas-types";

function WfCanvasEdgeImpl(props: EdgeProps<WfEdge>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data } = props;
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  return (
    <>
      <BaseEdge id={id} path={path} />
      {data?.on && (
        <EdgeLabelRenderer>
          {/* EdgeLabelRenderer 定位几何(transform)非间距,属 RF 官方范式 */}
          <div
            className="nodrag nopan pointer-events-none absolute"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            }}
          >
            <Badge
              variant={data.on === "true" ? "secondary" : "outline"}
              className={data.on === "true" ? "text-3xs" : "border-ring text-3xs"}
            >
              on={data.on}
            </Badge>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const WfCanvasEdge = memo(WfCanvasEdgeImpl);
