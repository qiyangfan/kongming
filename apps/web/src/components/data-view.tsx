/* 列表双形态标准件(mobile-experience B3.1,设计决策 D3):
   页面以一份列定义声明全部栏位(键/表头/渲染函数/卡片优先级),md+ 渲染
   ui/Table 表格(栏位与操作不因本契约减少),md- 渲染卡片列表(呈现卡片
   优先级最高的栏位,行操作收进溢出菜单,破坏性操作标红——确认链路由页面
   自身的 AlertDialog 承担,不在本组件内)。搜索/分页/筛选是页面级控件,
   保持在组件外。空态/加载态复用 EmptyState 单源。 */

import * as React from "react";
import { MoreHorizontal } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

export interface DataViewColumn<T> {
  key: string;
  header: React.ReactNode;
  /** 表格/卡片通用的单元格渲染;缺省按 String(row[key]) 显示 */
  render?: (row: T) => React.ReactNode;
  /** 卡片形态优先级:数值越小越优先;=1 者作卡片标题。缺省=不进卡片(仅表格) */
  cardPriority?: number;
  headClassName?: string;
  cellClassName?: string;
}

/** 行操作描述(单源声明):md+ 渲染行内按钮,md- 收进卡片溢出菜单 */
export interface DataViewAction<T> {
  /** 静态文案,或按行计算(如「上架/下架」随行状态、二次确认的武装态文案) */
  label: string | ((row: T) => string);
  onSelect: (row: T) => void;
  destructive?: boolean;
  /** 行级可见性谓词:返回 false 的行不渲染该操作(如「作废」仅 PENDING 行) */
  isVisible?: (row: T) => boolean;
  /** 行级禁用谓词:禁用仅降交互、保留可视反馈,避免「可点但静默无操作」 */
  isDisabled?: (row: T) => boolean;
  /** 禁用原因(按钮 title/菜单项提示),如「内置角色不可删除」 */
  disabledReason?: string;
}

interface DataViewProps<T> {
  columns: DataViewColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  actions?: DataViewAction<T>[];
  isLoading?: boolean;
  isEmpty?: boolean;
  loadingText?: string;
  emptyText?: string;
}

export function DataView<T>({
  columns,
  rows,
  rowKey,
  actions,
  isLoading = false,
  isEmpty = false,
  loadingText = "加载中…",
  emptyText = "无匹配数据",
}: DataViewProps<T>) {
  const cardColumns = columns
    .filter((c) => typeof c.cardPriority === "number")
    .sort((a, b) => (a.cardPriority ?? 99) - (b.cardPriority ?? 99));
  const [titleColumn, ...detailColumns] = cardColumns;

  const hasActions = actions !== undefined && actions.length > 0;
  const colSpan = columns.length + (hasActions ? 1 : 0);
  // 行级过滤两形态共用;卡片形态过滤后为空的行不渲染溢出菜单(防空菜单)
  const rowActions = (row: T) => (actions ?? []).filter((a) => !a.isVisible || a.isVisible(row));
  const actionLabel = (a: DataViewAction<T>, row: T): string =>
    typeof a.label === "function" ? a.label(row) : a.label;

  const renderCell = (col: DataViewColumn<T>, row: T): React.ReactNode =>
    col.render ? col.render(row) : String((row as Record<string, unknown>)[col.key] ?? "");

  return (
    <>
      {/* md+ 表格形态(与既有表格页同构) */}
      <div className="hidden md:block">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((col) => (
                <TableHead key={col.key} className={col.headClassName}>
                  {col.header}
                </TableHead>
              ))}
              {hasActions && <TableHead className="text-right">操作</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && (
              <TableRow>
                <TableCell colSpan={colSpan} className="p-0">
                  <EmptyState>{loadingText}</EmptyState>
                </TableCell>
              </TableRow>
            )}
            {rows.map((row) => (
              <TableRow key={rowKey(row)}>
                {columns.map((col) => (
                  <TableCell key={col.key} className={col.cellClassName}>
                    {renderCell(col, row)}
                  </TableCell>
                ))}
                {hasActions && (
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      {rowActions(row).map((a, i) => (
                        <Button
                          // actions 为页面级静态数组,序作 key 稳定(label 可按行变化)
                          key={i}
                          variant={a.destructive ? "ghost-destructive" : "ghost"}
                          size="sm"
                          disabled={a.isDisabled?.(row)}
                          title={a.isDisabled?.(row) ? a.disabledReason : undefined}
                          onClick={() => a.onSelect(row)}
                        >
                          {actionLabel(a, row)}
                        </Button>
                      ))}
                    </div>
                  </TableCell>
                )}
              </TableRow>
            ))}
            {isEmpty && !isLoading && (
              <TableRow>
                <TableCell colSpan={colSpan} className="p-0">
                  <EmptyState>{emptyText}</EmptyState>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {/* md- 卡片形态:标题栏位 + 详情栏位 + 溢出菜单(破坏性确认由页面承担) */}
      <div className="space-y-2 md:hidden">
        {isLoading && <EmptyState>{loadingText}</EmptyState>}
        {rows.map((row) => {
          const visible = rowActions(row);

          return (
            <div key={rowKey(row)} className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  {titleColumn ? renderCell(titleColumn, row) : null}
                </div>
                {visible.length > 0 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon-sm" aria-label="行操作">
                        <MoreHorizontal aria-hidden />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      {visible.map((a, i) => (
                        <DropdownMenuItem
                          // 同表格形态:静态数组以序作 key
                          key={i}
                          variant={a.destructive ? "destructive" : "default"}
                          disabled={a.isDisabled?.(row)}
                          onSelect={() => a.onSelect(row)}
                        >
                          {actionLabel(a, row)}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
              {detailColumns.length > 0 && (
                <dl className="mt-2 space-y-1.5">
                  {detailColumns.map((col) => (
                    <div key={col.key} className="flex items-baseline justify-between gap-3">
                      <dt className="shrink-0 text-xs text-muted-foreground">{col.header}</dt>
                      <dd className={cn("min-w-0 truncate text-right text-sm", col.cellClassName)}>
                        {renderCell(col, row)}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          );
        })}
        {isEmpty && !isLoading && <EmptyState>{emptyText}</EmptyState>}
      </div>
    </>
  );
}
