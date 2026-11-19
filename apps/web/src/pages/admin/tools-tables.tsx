/* 工具页的两张表:注册工具清单(只读)与执行审计记录。 */

import { Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { DataView } from "@/components/data-view";
import { formatDateTime, formatDuration } from "@/lib/datetime";
import type { ToolAuditPageOut, ToolListOut } from "@/lib/toolcall-api";

const STATUS_META: StatusMeta = {
  EXECUTED: { label: "已执行", variant: "default" },
  REJECTED: { label: "已拒绝", variant: "secondary" },
  ERROR: { label: "失败", variant: "destructive" },
  RATE_LIMITED: { label: "已限流", variant: "outline" },
  PENDING_CONFIRM: { label: "待确认", variant: "outline" },
  CONFIRM_REJECTED: { label: "已拒绝确认", variant: "secondary" },
  CONFIRM_EXPIRED: { label: "确认过期", variant: "secondary" },
};

type ToolRegistryItem = NonNullable<ToolListOut["tools"]>[number];
type ToolAuditItem = NonNullable<ToolAuditPageOut["items"]>[number];

/** 注册工具清单(只读)。 */
export function ToolRegistryTable({
  tools,
  isPending,
}: {
  tools: ToolRegistryItem[];
  isPending: boolean;
}) {
  return (
    <DataView
      rows={tools}
      rowKey={(t) => t.name}
      isLoading={isPending}
      isEmpty={!isPending && tools.length === 0}
      emptyText="暂无注册工具"
      columns={[
        {
          key: "name",
          header: "工具",
          cardPriority: 1,
          cellClassName: "font-mono text-xs font-medium",
          render: (t) => (
            <span className="flex items-center gap-1.5 font-mono text-xs font-medium">
              <Wrench className="size-3.5 text-muted-foreground" aria-hidden />
              {t.name}
            </span>
          ),
        },
        {
          key: "description",
          header: "描述",
          cardPriority: 3,
          cellClassName: "text-sm text-muted-foreground",
          render: (t) => (
            <span className="block max-w-md truncate" title={t.description}>
              {t.description}
            </span>
          ),
        },
        {
          key: "perm_code",
          header: "权限点",
          cellClassName: "font-mono text-xs",
        },
        {
          key: "write",
          header: "属性",
          cardPriority: 2,
          render: (t) => (
            <span className="flex gap-1">
              <Badge variant={t.write ? "destructive" : "secondary"}>
                {t.write ? "写" : "只读"}
              </Badge>
              {t.require_confirm && <Badge variant="outline">需确认</Badge>}
            </span>
          ),
        },
      ]}
    />
  );
}

/** 工具执行审计记录表。 */
export function ToolAuditTable({
  items,
  isPending,
}: {
  items: ToolAuditItem[];
  isPending: boolean;
}) {
  return (
    <DataView
      rows={items}
      rowKey={(it) => it.id}
      isLoading={isPending}
      isEmpty={!isPending && items.length === 0}
      emptyText="暂无工具执行记录"
      columns={[
        {
          key: "created_at",
          header: "时间",
          cardPriority: 2,
          cellClassName: "text-xs text-muted-foreground",
          render: (it) => formatDateTime(it.created_at, { seconds: true }),
        },
        {
          key: "tool_name",
          header: "工具",
          cardPriority: 1,
          cellClassName: "font-mono text-xs",
          render: (it) => <span className="font-mono text-xs">{it.tool_name}</span>,
        },
        {
          key: "status",
          header: "状态",
          cardPriority: 3,
          render: (it) => <StatusBadge meta={STATUS_META} value={it.status} />,
        },
        {
          key: "args_digest",
          header: "参数摘要",
          cellClassName: "text-xs text-muted-foreground",
          render: (it) => (
            <span className="block max-w-72 truncate" title={it.args_digest || undefined}>
              {it.args_digest || "—"}
            </span>
          ),
        },
        {
          key: "duration_ms",
          header: "耗时",
          cardPriority: 5,
          cellClassName: "text-xs",
          render: (it) => formatDuration(it.duration_ms ?? null),
        },
        {
          key: "datasource_key",
          header: "数据源",
          cardPriority: 4,
          cellClassName: "text-xs",
          render: (it) => it.datasource_key ?? "—",
        },
      ]}
    />
  );
}
