/* 团队列表:标识/名称/创建时间,行操作为成员与删除。 */

import { DataView } from "@kongming/ui/data-view";
import { formatDate } from "@kongming/api-client/datetime";
import type { TeamItem } from "@kongming/api-client/team-api";

interface TeamTableProps {
  rows: TeamItem[];
  isPending: boolean;
  isError: boolean;
  onMembers: (team: TeamItem) => void;
  onDelete: (team: TeamItem) => void;
}

/** 团队表。 */
export function TeamTable({ rows, isPending, isError, onMembers, onDelete }: TeamTableProps) {
  return (
    <DataView
      rows={rows}
      rowKey={(row) => row.id}
      isLoading={isPending}
      isEmpty={!isPending && !isError && rows.length === 0}
      emptyText="尚未加入任何团队。"
      columns={[
        {
          key: "team_key",
          header: "标识",
          cardPriority: 3,
          cellClassName: "font-mono text-xs",
          render: (row) => row.team_key,
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
          key: "created_at",
          header: "创建时间",
          cardPriority: 2,
          cellClassName: "text-xs text-muted-foreground",
          render: (row) => formatDate(row.created_at),
        },
      ]}
      actions={[
        { label: "成员", onSelect: onMembers },
        { label: "删除", destructive: true, onSelect: onDelete },
      ]}
    />
  );
}
