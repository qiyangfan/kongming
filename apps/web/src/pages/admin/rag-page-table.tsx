/* 知识库列表:筛选栏、DataView 列定义与分页。 */

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataView } from "@/components/data-view";
import { formatDateTime } from "@/lib/datetime";
import type { DatasourceOptionOut } from "@/lib/datasource-api";
import type { KnowledgeItemOut, TeamScope } from "@/lib/rag-api";
import type { TeamItem } from "@/lib/team-api";
import { Users as UsersIcon } from "lucide-react";

const SOURCE_LABEL: Record<string, string> = {
  INTROSPECTION: "自动扫描",
  AI_GENERATED: "AI 润色",
  MANUAL_UPLOAD: "人工上传",
  MANUAL_EDIT: "人工精修",
  FEEDBACK_REFINED: "反馈精修",
};

type RagTableProps = {
  items: KnowledgeItemOut[];
  isLoading: boolean;
  isEmpty: boolean;
  options: DatasourceOptionOut[] | undefined;
  dsFilter: string;
  onDsFilterChange: (v: string) => void;
  teamScope: TeamScope;
  onTeamScopeChange: (v: TeamScope) => void;
  myTeams: TeamItem[];
  total: number;
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  locateTable: string | null;
  canChangeOwnership: boolean;
  onEdit: (item: KnowledgeItemOut) => void;
  onOwnership: (item: KnowledgeItemOut) => void;
  onDelete: (item: KnowledgeItemOut) => void;
};

// ?table= 定位行高亮:DataView 无行级 className 口径,降为标题单元格高亮
function TableNameCell({
  item,
  locateTable,
}: {
  item: KnowledgeItemOut;
  locateTable: string | null;
}) {
  if (item.table_name === locateTable) {
    return <span className="bg-accent/50 px-1 font-mono text-xs">{item.table_name}</span>;
  }
  return <span className="font-mono text-xs">{item.table_name}</span>;
}

function RagFilters(props: RagTableProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        value={props.dsFilter || "all"}
        onValueChange={(v) => props.onDsFilterChange(v === "all" ? "" : v)}
      >
        <SelectTrigger className="w-44">
          <SelectValue placeholder="全部数据源" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部数据源</SelectItem>
          {(props.options ?? []).map((o) => (
            <SelectItem key={o.datasource_key} value={o.datasource_key}>
              {o.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={props.teamScope}
        onValueChange={(v) => props.onTeamScopeChange(v as TeamScope)}
      >
        <SelectTrigger className="w-40" aria-label="按归属筛选">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部归属</SelectItem>
          <SelectItem value="public">租户公共</SelectItem>
          {props.myTeams.map((t) => (
            <SelectItem key={t.id} value={t.id}>
              {t.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <span className="text-sm text-muted-foreground">共 {props.total} 条</span>
    </div>
  );
}

function RagPager(props: Pick<RagTableProps, "page" | "totalPages" | "onPageChange">) {
  return (
    <div className="flex items-center justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={props.page <= 1}
        onClick={() => props.onPageChange(props.page - 1)}
      >
        上一页
      </Button>
      <span className="text-sm text-muted-foreground">
        {props.page}/{props.totalPages}
      </span>
      <Button
        variant="outline"
        size="sm"
        disabled={props.page >= props.totalPages}
        onClick={() => props.onPageChange(props.page + 1)}
      >
        下一页
      </Button>
    </div>
  );
}

export function RagTable(props: RagTableProps) {
  return (
    <>
      <RagFilters {...props} />
      <DataView
        rows={props.items}
        rowKey={(item) => item.id}
        isLoading={props.isLoading}
        isEmpty={props.isEmpty}
        emptyText="暂无知识条目——可在右上角对数据源执行「扫描数据源」"
        columns={[
          {
            key: "datasource_key",
            header: "数据源",
            cellClassName: "font-mono text-xs",
          },
          {
            key: "schema_name",
            header: "schema",
            cellClassName: "font-mono text-xs",
          },
          {
            key: "table_name",
            header: "表名",
            cardPriority: 1,
            cellClassName: "font-mono text-xs",
            render: (item) => <TableNameCell item={item} locateTable={props.locateTable} />,
          },
          {
            key: "alias",
            header: "业务称谓",
            cardPriority: 4,
            cellClassName: "text-xs",
            render: (item) =>
              item.alias ? (
                <Badge variant="outline" className="max-w-40" title={item.alias}>
                  <span className="truncate">{item.alias}</span>
                </Badge>
              ) : (
                <span className="text-muted-foreground">—</span>
              ),
          },
          {
            key: "table_comment",
            header: "说明",
            cellClassName: "max-w-64 truncate",
            render: (item) => (
              <span className="block truncate" title={item.table_comment}>
                {item.table_comment}
              </span>
            ),
          },
          {
            key: "source_type",
            header: "来源",
            cardPriority: 2,
            render: (item) => (
              <Badge variant="secondary">
                {SOURCE_LABEL[item.source_type] ?? item.source_type}
              </Badge>
            ),
          },
          {
            key: "team_name",
            header: "归属",
            cardPriority: 5,
            render: (item) =>
              item.team_id ? (
                <Badge variant="outline" className="max-w-36 gap-1" title={item.team_name ?? ""}>
                  <UsersIcon className="size-3 shrink-0" aria-hidden />
                  <span className="truncate">{item.team_name ?? item.team_id}</span>
                </Badge>
              ) : (
                <span className="text-xs text-muted-foreground">公共</span>
              ),
          },
          {
            key: "updated_at",
            header: "更新时间",
            cardPriority: 3,
            cellClassName: "text-xs text-muted-foreground",
            render: (item) => formatDateTime(item.updated_at),
          },
        ]}
        actions={[
          { label: "编辑", onSelect: props.onEdit },
          // 归属变更入口(公共条目且无可写团队时无处可转,不显入口)
          {
            label: "归属",
            onSelect: props.onOwnership,
            isVisible: (item) => Boolean(item.team_id) || props.canChangeOwnership,
          },
          { label: "删除", destructive: true, onSelect: props.onDelete },
        ]}
      />
      <RagPager {...props} />
    </>
  );
}
