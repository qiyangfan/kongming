/* 工作流列表的列定义。 */

import { Badge } from "@kongming/ui/badge";
import type { DataViewColumn } from "@kongming/ui/data-view";
import { StatusSwitch } from "@kongming/ui/status-switch";
import type { WorkflowItem } from "@kongming/api-client/workflow-api";

/** 定义中的节点数;定义非法时按 0 计。 */
function nodeCount(row: WorkflowItem): number {
  const nodes = (row.definition as { nodes?: unknown[] }).nodes;
  return Array.isArray(nodes) ? nodes.length : 0;
}

export function workflowColumns(src: {
  togglePending: boolean;
  toggleKey: string | undefined;
  toggleError: Record<string, string>;
  onToggle: (row: WorkflowItem, enabled: boolean) => void;
}): DataViewColumn<WorkflowItem>[] {
  return [
    {
      key: "wf_key",
      header: "标识",
      cellClassName: "font-mono text-xs",
      render: (row) => row.wf_key,
    },
    {
      key: "name",
      header: "名称",
      cardPriority: 1,
      render: (row) => (
        <span className="block max-w-40 truncate" title={row.name}>
          {row.name}
        </span>
      ),
    },
    {
      key: "version",
      header: "版本",
      cardPriority: 3,
      render: (row) => <Badge variant="secondary">v{row.version}</Badge>,
    },
    {
      key: "nodes",
      header: "节点数",
      cardPriority: 4,
      render: (row) => nodeCount(row),
    },
    {
      key: "enabled",
      header: "启用",
      cardPriority: 2,
      render: (row) => (
        <StatusSwitch
          active={row.enabled}
          pending={src.togglePending && src.toggleKey === row.wf_key}
          error={src.toggleError[row.wf_key] || null}
          onToggle={() => src.onToggle(row, !row.enabled)}
          ariaLabel={`启停 ${row.name}`}
        />
      ),
    },
  ];
}
