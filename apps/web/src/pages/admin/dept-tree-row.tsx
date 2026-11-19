/* 部门树的一行:缩进、展开、状态与宽屏/窄屏两套行操作。 */

import { ChevronRight, MoreHorizontal } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { DeptNodeOut } from "@/lib/system-api";

/* 树形缩进用预定义类数组(禁内联 style;深度超限取模兜底) */
const DEPTH_PADS = ["pl-2", "pl-8", "pl-14", "pl-20", "pl-26", "pl-32"];

export interface DeptRowActions {
  onEdit: (n: DeptNodeOut) => void;
  onDelete: (n: DeptNodeOut) => void;
  onAddChild: (n: DeptNodeOut) => void;
}

/** 窄屏溢出菜单里的行操作。 */
function NarrowActions({ node, actions }: { node: DeptNodeOut; actions: DeptRowActions }) {
  return (
    <div className="md:hidden">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label={`${node.dept_name} 行操作`}>
            <MoreHorizontal aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => actions.onAddChild(node)}>加子级</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => actions.onEdit(node)}>编辑</DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onSelect={() => actions.onDelete(node)}>
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

/** 部门树行(有子节点时包一层可折叠容器,默认展开)。 */
export function TreeRow({
  node,
  depth,
  actions,
}: {
  node: DeptNodeOut;
  depth: number;
  actions: DeptRowActions;
}) {
  const kids = node.children ?? [];
  const hasChildren = kids.length > 0;
  const row = (
    <div
      className={`flex items-center gap-2 rounded-md py-1.5 pr-2 hover:bg-secondary/40 ${
        DEPTH_PADS[depth % DEPTH_PADS.length]
      }`}
    >
      {hasChildren ? (
        <CollapsibleTrigger className="text-muted-foreground hover:text-foreground">
          <ChevronRight
            className="size-4 transition-transform [[data-state=open]>&]:rotate-90"
            aria-hidden
          />
        </CollapsibleTrigger>
      ) : (
        <span className="size-4" />
      )}
      <span className="min-w-0 flex-1 truncate text-sm font-medium">{node.dept_name}</span>
      {node.status === "DISABLED" && <Badge variant="secondary">停用</Badge>}
      <span className="shrink-0 text-muted-foreground text-xs">#{node.sort}</span>
      {/* 宽屏行内按钮,窄屏收溢出菜单 */}
      <div className="hidden items-center gap-1 md:flex">
        <Button variant="ghost" size="sm" onClick={() => actions.onAddChild(node)}>
          加子级
        </Button>
        <Button variant="ghost" size="sm" onClick={() => actions.onEdit(node)}>
          编辑
        </Button>
        <Button variant="ghost-destructive" size="sm" onClick={() => actions.onDelete(node)}>
          删除
        </Button>
      </div>
      <NarrowActions node={node} actions={actions} />
    </div>
  );
  if (!hasChildren) return row;
  return (
    <Collapsible defaultOpen>
      {row}
      <CollapsibleContent>
        {kids.map((c) => (
          <TreeRow key={c.id} node={c} depth={depth + 1} actions={actions} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}
