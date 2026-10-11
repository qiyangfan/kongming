/* 画布 React Flow 类型窄化(107):RF 泛型节点/边 + 画布态桥接 + 六类节点图标。
   data.nodeType 才是工作流契约类型;RF Node.type 固定 "wf"(自定义组件注册名)。 */

import { Bot, Braces, CircleAlert, Database, GitBranch, Globe, Repeat } from "lucide-react";

import type { Edge, Node } from "@xyflow/react";

import { type WfNodeType } from "@/lib/workflow-canvas";

/** 六类节点的图标(lucide,禁 emoji) */
export const NODE_TYPE_ICONS: Record<WfNodeType, typeof CircleAlert> = {
  sql: Database,
  http: Globe,
  condition: GitBranch,
  loop: Repeat,
  transform: Braces,
  agent: Bot,
};

/** 自定义节点数据:契约类型 + config 全量 + depsExplicit(序列化保真)+ 错误定位
 *  (RF v12 要求数据满足 Record<string, unknown>,故继承索引签名) */
export interface WfNodeData extends Record<string, unknown> {
  nodeType: WfNodeType;
  config: Record<string, unknown>;
  depsExplicit: boolean;
  /** 服务端校验错误文案(定位命中时由画布面板写入;空=无错误) */
  errorText?: string;
}

export type WfFlowNode = Node<WfNodeData, "wf">;

/** 自定义边数据:on 为条件出边的分支值(非条件边恒 null) */
export interface WfEdgeData extends Record<string, unknown> {
  on: "true" | "false" | null;
}

export type WfEdge = Edge<WfEdgeData>;
