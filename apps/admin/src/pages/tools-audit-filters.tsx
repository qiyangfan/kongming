/* 工具执行审计的筛选条:工具、状态与日期区间。 */

import { Label } from "@kongming/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { DateRangeQuick } from "@kongming/ui/date-range-quick";

import { FilterDateRange } from "./admin-filter-bits";

interface ToolsAuditFiltersProps {
  toolNames: string[];
  statusOptions: { value: string; label: string }[];
  toolName: string;
  status: string;
  startDate: string;
  endDate: string;
  onToolName: (value: string) => void;
  onStatus: (value: string) => void;
  onDateRange: (startDate: string, endDate: string) => void;
}

/** 审计筛选条:各条件变化都回到第一页。 */
export function ToolsAuditFilters(props: ToolsAuditFiltersProps) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="space-y-1">
        <Label htmlFor="flt-tool" className="text-xs">
          工具
        </Label>
        <Select
          value={props.toolName || "__all__"}
          onValueChange={(v) => props.onToolName(v === "__all__" ? "" : v)}
        >
          <SelectTrigger id="flt-tool" className="w-52" aria-label="按工具筛选">
            <SelectValue placeholder="全部工具" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">全部工具</SelectItem>
            {props.toolNames.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label htmlFor="flt-status" className="text-xs">
          状态
        </Label>
        <Select
          value={props.status || "__all__"}
          onValueChange={(v) => props.onStatus(v === "__all__" ? "" : v)}
        >
          <SelectTrigger id="flt-status" className="w-40" aria-label="按状态筛选">
            <SelectValue placeholder="全部状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">全部状态</SelectItem>
            {props.statusOptions.map((m) => (
              <SelectItem key={m.value} value={m.value}>
                {m.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <FilterDateRange
        startDate={props.startDate}
        endDate={props.endDate}
        startId="flt-start"
        endId="flt-end"
        onChange={props.onDateRange}
      />
      <DateRangeQuick
        onPick={(s, e) => {
          props.onDateRange(s, e);
        }}
      />
    </div>
  );
}
