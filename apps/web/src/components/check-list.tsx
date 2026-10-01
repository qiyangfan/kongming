import { EmptyState } from "@/components/empty-state";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

/** 多选面板的候选项:label 主文案(可含层级缩进前缀),hint 为辅助标识(如权限点编码)。 */
export type CheckOption = { id: string; label: string; hint?: string; title?: string };

/**
 * 勾选项组(全站单源,宪法 Article 6):带边框滚动容器 + Checkbox 行 + 空态走 EmptyState。
 * 「角色权限绑定」与「角色自定义部门」两处同类面板的公共出处——禁逐页手搓 label+checkbox。
 * 行宽用 min-w-0 + truncate 收敛,窄屏(375)不横向溢出;行高 min-h-11 保触控热区 ≥44(B1.2);
 * 截断项一律带 title,长名称必须可回读(整行可点,故 title 挂在 label 上)。
 */
export function CheckList({
  options,
  selected,
  onChange,
  emptyText = "暂无可选项",
  className,
}: {
  options: CheckOption[];
  selected: string[];
  onChange: (ids: string[]) => void;
  emptyText?: string;
  className?: string;
}) {
  if (options.length === 0) return <EmptyState className="py-6">{emptyText}</EmptyState>;
  return (
    <div className={cn("max-h-40 space-y-1 overflow-y-auto rounded-md border p-2", className)}>
      {options.map((o) => (
        <label
          key={o.id}
          title={o.title ?? o.label}
          className="flex min-h-11 min-w-0 items-center gap-2 text-sm"
        >
          <Checkbox
            checked={selected.includes(o.id)}
            onCheckedChange={(checked) =>
              onChange(checked === true ? [...selected, o.id] : selected.filter((x) => x !== o.id))
            }
          />
          <span className="min-w-0 truncate">{o.label}</span>
          {o.hint && <span className="text-muted-foreground shrink-0 text-xs">{o.hint}</span>}
        </label>
      ))}
    </div>
  );
}
