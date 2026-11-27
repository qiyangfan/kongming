/* 管理页共用的筛选小件:日期区间输入与「越界回退一页」的分页自愈。 */

import { useEffect } from "react";

import { Input } from "@kongming/ui/input";
import { Label } from "@kongming/ui/label";

interface FilterDateRangeProps {
  startDate: string;
  endDate: string;
  onChange: (startDate: string, endDate: string) => void;
  startId?: string;
  endId?: string;
  className?: string;
}

/** 开始/结束日期两个原生日期输入,任一变化即回传整对。 */
export function FilterDateRange({
  startDate,
  endDate,
  onChange,
  startId,
  endId,
  className = "w-40",
}: FilterDateRangeProps) {
  return (
    <>
      <div className="space-y-1">
        <Label htmlFor={startId} className="text-xs">
          开始日期
        </Label>
        <Input
          id={startId}
          type="date"
          value={startDate}
          onChange={(e) => onChange(e.target.value, endDate)}
          className={className}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={endId} className="text-xs">
          结束日期
        </Label>
        <Input
          id={endId}
          type="date"
          value={endDate}
          onChange={(e) => onChange(startDate, e.target.value)}
          className={className}
        />
      </div>
    </>
  );
}

/**
 * 分页越界自愈:当前页超出末页且该页为空时回退一页。
 * 删除末页条目或过滤后总页数收缩时避免停在空页。
 */
export function usePageFallback(
  data: { total?: number; items: unknown[] } | undefined,
  page: number,
  setPage: React.Dispatch<React.SetStateAction<number>>,
): void {
  useEffect(() => {
    const t = data?.total;
    if (t !== undefined && t > 0 && page > 1 && data?.items.length === 0) {
      setPage((p) => p - 1);
    }
  }, [data, page, setPage]);
}
