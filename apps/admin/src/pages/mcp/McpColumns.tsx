/* MCP 服务列表的列定义:标识/名称/Endpoint/工具(含同步错误与行内提示)/启停。 */

import { Badge } from "@kongming/ui/badge";
import type { DataViewColumn } from "@kongming/ui/data-view";
import { StatusSwitch } from "@kongming/ui/status-switch";
import type { McpServerOut } from "@kongming/api-client/mcp-api";

export function mcpColumns(src: {
  rowNote: Record<string, string>;
  togglePending: boolean;
  toggleRowId: string | undefined;
  toggleError: Record<string, string>;
  onToggle: (row: McpServerOut) => void;
}): DataViewColumn<McpServerOut>[] {
  return [
    {
      key: "server_key",
      header: "标识",
      cardPriority: 4,
      cellClassName: "font-mono text-xs",
      render: (row) => row.server_key,
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
      key: "endpoint",
      header: "Endpoint",
      cardPriority: 2, // 连接标识(服务地址)窄屏卡片可见
      render: (row) => (
        <span className="block max-w-56 truncate font-mono text-xs" title={row.endpoint}>
          {row.endpoint}
        </span>
      ),
    },
    {
      key: "tools",
      header: "工具",
      cardPriority: 3,
      render: (row) => (
        <>
          <Badge variant="secondary">{row.tools.length} 个</Badge>
          {row.last_sync_error && (
            <p
              className="mt-1 max-w-52 truncate text-xs text-destructive"
              title={row.last_sync_error}
            >
              {row.last_sync_error}
            </p>
          )}
          {src.rowNote[row.id] && (
            <p
              className="mt-1 max-w-72 truncate text-xs text-muted-foreground"
              title={src.rowNote[row.id]}
              role="status"
            >
              {src.rowNote[row.id]}
            </p>
          )}
        </>
      ),
    },
    {
      key: "enabled",
      header: "启用",
      cardPriority: 2,
      render: (row) => (
        <StatusSwitch
          active={row.enabled}
          pending={src.togglePending && src.toggleRowId === row.id}
          error={src.toggleError[row.id] || null}
          onToggle={() => src.onToggle(row)}
          ariaLabel={`启停 ${row.name}`}
        />
      ),
    },
  ];
}
