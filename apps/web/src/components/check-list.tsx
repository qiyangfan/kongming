import { EmptyState } from "@/components/empty-state";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

/** 多选面板的候选项:label 主文案(可含层级缩进前缀),hint 为辅助标识(如权限点编码)。 */
export type CheckOption = { id: string; label: string; hint?: string; title?: string };

/** 可复用的带边框勾选列表；内容超高时滚动，空数据使用 EmptyState。 */
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
