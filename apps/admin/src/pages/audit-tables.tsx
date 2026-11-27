/* 审计页的两张表:SQL 审计与操作日志。 */

import { DataView } from "@kongming/ui/data-view";
import { StatusBadge, type StatusMeta } from "@kongming/ui/status-badge";
import type { operLogPage, sqlAuditPage } from "@kongming/api-client/audit-api";
import { displayUser } from "@kongming/api-client/display-user";
import { formatDateTime, formatDuration } from "@kongming/api-client/datetime";

export type SqlItem = NonNullable<Awaited<ReturnType<typeof sqlAuditPage>>["items"]>[number];
export type OperItem = NonNullable<Awaited<ReturnType<typeof operLogPage>>["items"]>[number];

const VERDICT_META: StatusMeta = {
  PASS: { label: "通过", variant: "default" },
  BLOCKED: { label: "拦截", variant: "secondary" },
  ERROR: { label: "失败", variant: "outline" },
};

/** 用户列:无 user_id 记为「系统」,超宽截断并悬浮回读全名。 */
function UserCell({
  item,
}: {
  item: { user_id?: string | null; user_nickname?: string | null; user_username?: string | null };
}) {
  const name = item.user_id ? displayUser(item.user_nickname, item.user_username) : "系统";
  return (
    <span className="block max-w-32 truncate" title={name}>
      {name}
    </span>
  );
}

interface SqlAuditTableProps {
  items: SqlItem[];
  isPending: boolean;
  onDetail: (item: SqlItem) => void;
}

/** SQL 审计表。 */
export function SqlAuditTable({ items, isPending, onDetail }: SqlAuditTableProps) {
  return (
    <DataView
      rows={items}
      rowKey={(it) => it.id}
      isLoading={isPending}
      isEmpty={!isPending && items.length === 0}
      emptyText="暂无审计记录。"
      columns={[
        {
          key: "created_at",
          header: "时间",
          cardPriority: 2,
          cellClassName: "whitespace-nowrap text-muted-foreground",
          render: (it) => formatDateTime(it.created_at, { seconds: true }),
        },
        { key: "user", header: "用户", cardPriority: 3, render: (it) => <UserCell item={it} /> },
        { key: "tenant_key", header: "租户" },
        { key: "datasource_key", header: "数据源" },
        {
          key: "question",
          header: "问句",
          cardPriority: 1,
          render: (it) => (
            <span className="block max-w-56 truncate" title={it.question ?? undefined}>
              {it.question ?? "—"}
            </span>
          ),
        },
        {
          key: "verdict",
          header: "判定",
          cardPriority: 4,
          render: (it) => <StatusBadge meta={VERDICT_META} value={it.verdict} />,
        },
        {
          key: "duration_ms",
          header: "耗时",
          cardPriority: 5,
          cellClassName: "tabular-nums",
          render: (it) => formatDuration(it.duration_ms),
        },
      ]}
      actions={[{ label: "SQL", onSelect: onDetail }]}
    />
  );
}

interface OperLogTableProps {
  items: OperItem[];
  isPending: boolean;
  onDetail: (item: OperItem) => void;
}

/** 操作日志表。 */
export function OperLogTable({ items, isPending, onDetail }: OperLogTableProps) {
  return (
    <DataView
      rows={items}
      rowKey={(it) => it.id}
      isLoading={isPending}
      isEmpty={!isPending && items.length === 0}
      emptyText="暂无操作记录。"
      columns={[
        {
          key: "created_at",
          header: "时间",
          cardPriority: 2,
          cellClassName: "whitespace-nowrap text-muted-foreground",
          render: (it) => formatDateTime(it.created_at, { seconds: true }),
        },
        { key: "user", header: "用户", cardPriority: 3, render: (it) => <UserCell item={it} /> },
        {
          key: "action",
          header: "动作",
          cardPriority: 1,
          cellClassName: "font-mono text-xs",
          render: (it) => <span className="font-mono text-xs">{it.action}</span>,
        },
        {
          key: "target",
          header: "目标",
          cardPriority: 4,
          render: (it) => (
            <span
              className="block max-w-48 truncate font-mono text-xs"
              title={it.target ?? undefined}
            >
              {it.target ?? "—"}
            </span>
          ),
        },
        {
          key: "ip",
          header: "IP",
          cardPriority: 5,
          cellClassName: "font-mono text-xs",
          render: (it) => it.ip ?? "—",
        },
      ]}
      actions={[{ label: "详情", onSelect: onDetail }]}
    />
  );
}
