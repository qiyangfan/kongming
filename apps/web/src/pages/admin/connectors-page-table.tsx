/* 连接器列表:声明要点、鉴权掩码、写语义与启停。 */

import { Badge } from "@/components/ui/badge";
import { DataView } from "@/components/data-view";
import { StatusSwitch } from "@/components/status-switch";
import type { ConnectorOut } from "@/lib/connector-api";
import { AUTH_LABELS } from "./connectors-page-model";

export function ConnectorsTable({
  rows,
  isLoading,
  isEmpty,
  togglePending,
  togglePendingId,
  toggleError,
  onToggle,
  onTest,
  onEdit,
  onDelete,
}: {
  rows: ConnectorOut[];
  isLoading: boolean;
  isEmpty: boolean;
  togglePending: boolean;
  togglePendingId: string | undefined;
  toggleError: Record<string, string>;
  onToggle: (row: ConnectorOut) => void;
  onTest: (row: ConnectorOut) => void;
  onEdit: (row: ConnectorOut) => void;
  onDelete: (row: ConnectorOut) => void;
}) {
  return (
    <DataView
      rows={rows}
      rowKey={(row) => row.id}
      isLoading={isLoading}
      isEmpty={isEmpty}
      emptyText="尚未注册连接器。"
      columns={[
        {
          key: "key",
          header: "标识",
          cardPriority: 3,
          cellClassName: "font-mono text-xs",
          render: (row) => row.key,
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
          header: "方法与路径",
          cardPriority: 2,
          render: (row) => (
            <span className="block max-w-64 truncate font-mono text-xs" title={row.path_template}>
              <Badge variant="outline" className="mr-1.5">
                {row.method}
              </Badge>
              {row.path_template}
            </span>
          ),
        },
        {
          key: "auth",
          header: "鉴权",
          cardPriority: 4,
          render: (row) => (
            <span className="text-xs">
              {AUTH_LABELS[row.auth_type] ?? row.auth_type}
              {row.has_secret && (
                <Badge variant="secondary" className="ml-1.5 font-mono">
                  {row.secret_mask || "****"}
                </Badge>
              )}
            </span>
          ),
        },
        {
          key: "confirm",
          header: "写语义",
          cardPriority: 5,
          render: (row) =>
            row.requires_confirmation ? (
              <Badge variant="secondary">需确认</Badge>
            ) : (
              <Badge variant="outline">读</Badge>
            ),
        },
        {
          key: "enabled",
          header: "启用",
          cardPriority: 2,
          render: (row) => (
            <StatusSwitch
              active={row.enabled === "ENABLED"}
              pending={togglePending && togglePendingId === row.id}
              error={toggleError[row.id] || null}
              onToggle={() => onToggle(row)}
              ariaLabel={`启停 ${row.name}`}
            />
          ),
        },
      ]}
      actions={[
        { label: "测试调用", onSelect: (row) => onTest(row) },
        { label: "编辑", onSelect: (row) => onEdit(row) },
        { label: "删除", destructive: true, onSelect: (row) => onDelete(row) },
      ]}
    />
  );
}
