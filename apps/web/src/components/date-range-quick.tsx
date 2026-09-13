/* 日期筛选快捷档(近 7 天/近 30 天,含今天):一键填充开始/结束日期。
   审计/护栏/工具等日期筛选区共用(≥2 处同类 UI 抽公共组件)。日期串按本地时区生成;
   区间的日界语义(本地/UTC 零点)由消费页的换算(如 endExclusive)决定,本组件不解释。 */

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";

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

/** 「快捷」筛选字段:与同区其他 Label+控件字段同构(items-end 对齐) */
export function DateRangeQuick({ onPick }: { onPick: (start: string, end: string) => void }) {
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
