/* 护栏触发审计区:层/动作/日期筛选 + 触发记录表 + 分页。 */

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import { DataView } from "@/components/data-view";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ErrorAlert } from "@/components/error-alert";
import { DateRangeQuick } from "@/components/date-range-quick";
import { formatDateTime } from "@/lib/datetime";

import { FilterDateRange } from "./admin-filter-bits";
import { ACTION_META, TYPE_LABELS } from "./guardrail-labels";

interface GuardrailAuditItem {
  id: string;
  created_at: string;
  guardrail_type: string;
  rule_name: string;
  action: string;
  content_digest?: string | null;
}

interface GuardrailAuditSectionProps {
  items: GuardrailAuditItem[];
  total: number;
  page: number;
  totalPages: number;
  isPending: boolean;
  isError: boolean;
  ftype: string;
  faction: string;
  startDate: string;
  endDate: string;
  onFtype: (value: string) => void;
  onFaction: (value: string) => void;
  onDateRange: (startDate: string, endDate: string) => void;
  onPageChange: (page: number) => void;
}

/** 一层筛选下拉(带 id 便于 Label 关联)。 */
function LayerFilter({ ftype, onFtype }: Pick<GuardrailAuditSectionProps, "ftype" | "onFtype">) {
  return (
    <div className="space-y-1">
      <Label htmlFor="gr-type" className="text-xs">
        层
      </Label>
      <Select value={ftype || "__all__"} onValueChange={(v) => onFtype(v === "__all__" ? "" : v)}>
        <SelectTrigger id="gr-type" className="w-36" aria-label="按层筛选">
          <SelectValue placeholder="全部" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部</SelectItem>
          {Object.entries(TYPE_LABELS).map(([k, l]) => (
            <SelectItem key={k} value={k}>
              {l}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** 动作筛选下拉。 */
function ActionFilter({
  faction,
  onFaction,
}: Pick<GuardrailAuditSectionProps, "faction" | "onFaction">) {
  return (
    <div className="space-y-1">
      <Label htmlFor="gr-action" className="text-xs">
        动作
      </Label>
      <Select
        value={faction || "__all__"}
        onValueChange={(v) => onFaction(v === "__all__" ? "" : v)}
      >
        <SelectTrigger id="gr-action" className="w-36" aria-label="按动作筛选">
          <SelectValue placeholder="全部" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">全部</SelectItem>
          <SelectItem value="blocked">拦截</SelectItem>
          <SelectItem value="masked">脱敏</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

/** 触发审计整区。 */
export function GuardrailAuditSection(props: GuardrailAuditSectionProps) {
  const { items, total, page, totalPages, isPending, isError } = props;
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-foreground">触发审计</h3>
      <div className="flex flex-wrap items-end gap-3">
        <LayerFilter ftype={props.ftype} onFtype={props.onFtype} />
        <ActionFilter faction={props.faction} onFaction={props.onFaction} />
        <FilterDateRange
          startDate={props.startDate}
          endDate={props.endDate}
          startId="gr-start"
          endId="gr-end"
          onChange={props.onDateRange}
        />
        <DateRangeQuick
          onPick={(s, e) => {
            props.onDateRange(s, e);
            props.onPageChange(1);
          }}
        />
      </div>

      {isError && <ErrorAlert />}
      <DataView
        rows={items}
        rowKey={(it) => it.id}
        isLoading={isPending}
        isEmpty={!isPending && items.length === 0}
        emptyText="暂无护栏触发记录"
        columns={[
          {
            key: "created_at",
            header: "时间",
            cardPriority: 2,
            cellClassName: "text-xs text-muted-foreground",
            render: (it) => formatDateTime(it.created_at, { seconds: true }),
          },
          {
            key: "guardrail_type",
            header: "层",
            cardPriority: 3,
            render: (it) => (
              <Badge variant="secondary">
                {TYPE_LABELS[it.guardrail_type] ?? it.guardrail_type}
              </Badge>
            ),
          },
          {
            key: "rule_name",
            header: "规则",
            cardPriority: 1,
            cellClassName: "font-mono text-xs",
            render: (it) => <span className="font-mono text-xs">{it.rule_name}</span>,
          },
          {
            key: "action",
            header: "动作",
            cardPriority: 4,
            render: (it) => (
              <StatusBadge meta={ACTION_META} value={it.action} fallbackVariant="default" />
            ),
          },
          {
            key: "content_digest",
            header: "内容摘要(脱敏)",
            cellClassName: "text-xs text-muted-foreground",
            render: (it) => (
              <span className="block max-w-96 truncate" title={it.content_digest || undefined}>
                {it.content_digest || "—"}
              </span>
            ),
          },
        ]}
      />

      <Pagination
        page={page}
        pageCount={totalPages}
        total={total}
        onPageChange={props.onPageChange}
      />
    </div>
  );
}
