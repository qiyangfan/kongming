/* 提供近 7 天和近 30 天筛选；日期按本地时区生成，日界换算由调用页面处理。 */

import { Button } from "@kongming/ui/button";
import { Label } from "@kongming/ui/label";

/** 本地时区日期串 YYYY-MM-DD;offsetDays 为相对今天的偏移(0=今天) */
function localDateStr(offsetDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const PRESETS = [
  { label: "近 7 天", days: 7 },
  { label: "近 30 天", days: 30 },
];

/** 「快捷」筛选字段,与同区其他 Label+控件字段同构 */
export function DateRangeQuick({
  onPick,
}: {
  onPick: (start: string, end: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">快捷</Label>
      <div className="flex gap-1">
        {PRESETS.map((p) => (
          <Button
            key={p.days}
            type="button"
            variant="outline"
            onClick={() => onPick(localDateStr(-(p.days - 1)), localDateStr(0))}
          >
            {p.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
