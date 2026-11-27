/* data.nodeType 是工作流节点类型；React Flow 的 Node.type 用于选择自定义渲染组件。 */

import {
  Bot,
  Braces,
  CircleAlert,
  Database,
  GitBranch,
  Globe,
  Repeat,
} from "lucide-react";

import type { Edge, Node } from "@xyflow/react";

import { type WfNodeType } from "@kongming/api-client/workflow-canvas";

/** 六类节点类型对应的图标 */
export const NODE_TYPE_ICONS: Record<WfNodeType, typeof CircleAlert> = {
  sql: Database,
  http: Globe,
  condition: GitBranch,
  loop: Repeat,
  transform: Braces,
  agent: Bot,
};

/** React Flow 自定义节点数据(RF v12 要求数据满足 Record<string, unknown>,故继承索引签名)。 */
export interface WfNodeData extends Record<string, unknown> {
  nodeType: WfNodeType;
  config: Record<string, unknown>;
  /** 原定义是否显式声明 depends_on;导出时据此保留空的 depends_on 字段 */
  depsExplicit: boolean;
  /** 服务端校验错误文案(定位命中时由画布面板写入;空=无错误) */
  errorText?: string | undefined;
}

export type WfFlowNode = Node<WfNodeData, "wf">;

/** 自定义边数据:on 为条件出边的分支值(非条件边恒 null) */
export interface WfEdgeData extends Record<string, unknown> {
  on: "true" | "false" | null;
}

export type WfEdge = Edge<WfEdgeData>;
