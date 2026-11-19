/* 触发器列表:DataView 列定义与行操作(启停、查看历史、编辑、删除)。 */

import { Badge } from "@/components/ui/badge";
import { DataView } from "@/components/data-view";
import { StatusSwitch } from "@/components/status-switch";
import type { TriggerItem } from "@/lib/trigger-api";
import { formatDateTime } from "@/lib/datetime";
import { TYPE_LABELS } from "./triggers-page-model";

type Props = {
  rows: TriggerItem[];
  isLoading: boolean;
  isEmpty: boolean;
  togglePending: boolean;
  togglePendingId: string | undefined;
  toggleError: Record<string, string>;
  onToggle: (row: TriggerItem) => void;
  onHistory: (row: TriggerItem) => void;
  onEdit: (row: TriggerItem) => void;
  onDelete: (row: TriggerItem) => void;
};

// 按类型渲染调度/地址列:定时显示 cron 与下次执行,邮件显示 IMAP 连接要点,webhook 显示专属地址
function renderSchedule(row: TriggerItem) {
  if (row.type === "CRON") {
    return (
      <>
        <span className="block truncate" title={row.cron_expr ?? undefined}>
          {row.cron_expr}
        </span>
        {row.next_fire_at && (
          <p className="whitespace-normal text-muted-foreground">
            下次 {formatDateTime(row.next_fire_at)}
          </p>
        )}
      </>
    );
  }
  if (row.type === "EMAIL") {
    return (
      <>
        {/* 邮件形态展示 IMAP 连接要点(凭证永不回显) */}
        <span className="block truncate" title={row.imap_host}>
          {row.imap_host}/{row.imap_folder}
        </span>
        <p className="whitespace-normal text-muted-foreground">
          每 {row.imap_poll_interval_sec} 秒轮询
          {row.next_fire_at ? ` · 下次 ${formatDateTime(row.next_fire_at)}` : ""}
        </p>
      </>
    );
  }
  return (
    <span className="block truncate" title={`/webhooks/${row.webhook_path}`}>
      /webhooks/{row.webhook_path}
    </span>
  );
}

export function TriggersTable(props: Props) {
  return (
    <DataView
      rows={props.rows}
      rowKey={(row) => row.id}
      isLoading={props.isLoading}
      isEmpty={props.isEmpty}
      emptyText="尚未配置触发器。"
      columns={[
        {
          key: "tg_key",
          header: "标识",
          cardPriority: 1,
          cellClassName: "font-mono text-xs",
          render: (row) => row.tg_key,
        },
        {
          key: "type",
          header: "类型",
          cardPriority: 4,
          render: (row) => <Badge variant="secondary">{TYPE_LABELS[row.type] ?? row.type}</Badge>,
        },
        {
          key: "target",
          header: "目标",
          cellClassName: "text-xs",
          render: (row) => `${row.target_type}:${row.target_key}`,
        },
        {
          key: "schedule",
          header: "调度/地址",
          cardPriority: 3,
          cellClassName: "max-w-56 truncate font-mono text-xs",
          render: renderSchedule,
        },
        {
          key: "enabled",
          header: "启用",
          cardPriority: 2,
          render: (row) => (
            <StatusSwitch
              active={row.enabled}
              pending={props.togglePending && props.togglePendingId === row.id}
              error={props.toggleError[row.id] || null}
              onToggle={() => props.onToggle(row)}
              ariaLabel={`启停 ${row.name}`}
            />
          ),
        },
      ]}
      actions={[
        {
          label: "历史",
          onSelect: (row) => props.onHistory(row),
        },
        {
          label: "编辑",
          onSelect: (row) => props.onEdit(row),
        },
        {
          label: "删除",
          destructive: true,
          onSelect: (row) => props.onDelete(row),
        },
      ]}
    />
  );
}
