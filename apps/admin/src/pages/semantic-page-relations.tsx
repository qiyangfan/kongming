/* 语义层「表关系」页签:关系清单、扫描入口与手工新增关系表单。 */

import { Plus, RefreshCw, Loader2, X } from "lucide-react";

import { Badge } from "@kongming/ui/badge";
import { Button } from "@kongming/ui/button";
import { Input } from "@kongming/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { Label } from "@kongming/ui/label";
import { DataView } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import type { RelationItem } from "@kongming/api-client/semantic-api";

export type TableOption = { value: string; label: string };
export type JoinPair = { left: string; right: string };

type QueryLike<T> = { data: T | undefined; isPending: boolean; isError: boolean };

// 表下拉占位:按知识条目加载状态给出可行动文案
function tablePlaceholder(q: QueryLike<unknown[]>, options: TableOption[], base: string): string {
  if (q.isError) return "候选加载失败";
  if (q.isPending) return "加载中…";
  return options.length ? base : "暂无知识条目";
}

function TableSelect({
  label,
  ariaLabel,
  value,
  onChange,
  q,
  options,
  placeholder,
}: {
  label: string;
  ariaLabel: string;
  value: string;
  onChange: (v: string) => void;
  q: QueryLike<unknown[]>;
  options: TableOption[];
  placeholder: string;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {/* 表引用取知识条目下拉,展示业务别名(候选与后端校验同源) */}
      <Select value={value} onValueChange={onChange} disabled={q.isPending}>
        <SelectTrigger aria-label={ariaLabel} className="w-56">
          <SelectValue placeholder={tablePlaceholder(q, options, placeholder)} />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

// 列下拉;条目缺列清单时降级手输
function ColumnField({
  ariaLabel,
  value,
  options,
  placeholder,
  onChange,
}: {
  ariaLabel: string;
  value: string;
  options: string[];
  placeholder: string;
  onChange: (v: string) => void;
}) {
  if (options.length === 0) {
    return (
      <Input
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-44"
        placeholder={placeholder}
      />
    );
  }
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={ariaLabel} className="w-44">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((c) => (
          <SelectItem key={c} value={c}>
            {c}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// JOIN 键对行(多列复合键=多行)
function JoinPairRows({
  pairs,
  srcCols,
  tgtCols,
  onPatch,
  onRemove,
  onAdd,
}: {
  pairs: JoinPair[];
  srcCols: string[];
  tgtCols: string[];
  onPatch: (idx: number, patch: Partial<JoinPair>) => void;
  onRemove: (idx: number) => void;
  onAdd: () => void;
}) {
  return (
    <div className="space-y-2">
      {pairs.map((pair, idx) => (
        <div key={idx} className="flex items-center gap-2">
          <span className="w-14 text-xs text-muted-foreground">
            {idx === 0 ? "关联字段" : `字段 ${idx + 1}`}
          </span>
          <ColumnField
            ariaLabel={`左列 ${idx + 1}`}
            value={pair.left}
            options={srcCols}
            placeholder={srcCols.length ? "左表列" : "左表列(条目缺列清单,手输)"}
            onChange={(v) => onPatch(idx, { left: v })}
          />
          <span className="text-xs text-muted-foreground">=</span>
          <ColumnField
            ariaLabel={`右列 ${idx + 1}`}
            value={pair.right}
            options={tgtCols}
            placeholder={tgtCols.length ? "右表列" : "右表列(条目缺列清单,手输)"}
            onChange={(v) => onPatch(idx, { right: v })}
          />
          {pairs.length > 1 && (
            <Button
              size="icon"
              variant="ghost"
              aria-label={`移除键对 ${idx + 1}`}
              onClick={() => onRemove(idx)}
            >
              <X className="size-3.5" aria-hidden />
            </Button>
          )}
        </div>
      ))}
      <div>
        <Button size="xs" variant="outline" onClick={onAdd}>
          <Plus className="size-3.5" aria-hidden />
          添加关联字段(多字段组合)
        </Button>
      </div>
    </div>
  );
}

function RelationsTable({
  relations,
  onDelete,
}: {
  relations: QueryLike<RelationItem[]>;
  onDelete: (r: RelationItem) => void;
}) {
  return (
    <>
      {relations.isError && <ErrorAlert />}
      <DataView
        rows={relations.data ?? []}
        rowKey={(r) => r.id}
        isLoading={relations.isPending}
        isEmpty={!relations.isPending && relations.data?.length === 0}
        emptyText="暂无关系——点「扫描表关联」或手工新增"
        columns={[
          {
            key: "source_table",
            header: "左表",
            cardPriority: 1,
            cellClassName: "font-mono text-xs",
            render: (r) => (
              <span className="block max-w-48 truncate font-mono text-xs" title={r.source_table}>
                {r.source_table}
              </span>
            ),
          },
          {
            key: "target_table",
            header: "右表",
            cardPriority: 2,
            cellClassName: "font-mono text-xs",
            render: (r) => (
              <span className="block max-w-48 truncate font-mono text-xs" title={r.target_table}>
                {r.target_table}
              </span>
            ),
          },
          {
            key: "join_keys",
            header: "关联字段",
            cardPriority: 3,
            cellClassName: "font-mono text-xs",
            render: (r) => (
              <span
                className="block max-w-40 truncate font-mono text-xs"
                title={
                  r.join_keys.map((k) => `${k.left_col}=${k.right_col}`).join(", ") || undefined
                }
              >
                {r.join_keys.map((k) => `${k.left_col}=${k.right_col}`).join(", ")}
              </span>
            ),
          },
          {
            key: "source",
            header: "来源",
            cardPriority: 4,
            render: (r) => (
              <Badge variant={r.source === "manual" ? "default" : "secondary"}>
                {r.source === "manual" ? "手工" : "自动扫描"}
              </Badge>
            ),
          },
        ]}
        actions={[
          {
            label: "删除",
            destructive: true,
            // 仅手工行渲染删除(introspection 行只读)
            isVisible: (r) => r.source === "manual",
            onSelect: (r) => onDelete(r),
          },
        ]}
      />
    </>
  );
}

export function SemanticRelationsTab({
  relations,
  knowledge,
  tableOptions,
  columnsOf,
  srcTable,
  tgtTable,
  onSrcTableChange,
  onTgtTableChange,
  joinPairs,
  onPatchPair,
  onRemovePair,
  onAddPair,
  addPending,
  onAdd,
  scanPending,
  onScan,
  onDelete,
}: {
  relations: QueryLike<RelationItem[]>;
  knowledge: QueryLike<unknown[]>;
  tableOptions: TableOption[];
  columnsOf: (ref: string) => string[];
  srcTable: string;
  tgtTable: string;
  onSrcTableChange: (v: string) => void;
  onTgtTableChange: (v: string) => void;
  joinPairs: JoinPair[];
  onPatchPair: (idx: number, patch: Partial<JoinPair>) => void;
  onRemovePair: (idx: number) => void;
  onAddPair: () => void;
  addPending: boolean;
  onAdd: () => void;
  scanPending: boolean;
  onScan: () => void;
  onDelete: (r: RelationItem) => void;
}) {
  const canAdd =
    Boolean(srcTable) &&
    Boolean(tgtTable) &&
    joinPairs.some((p) => p.left.trim() && p.right.trim()) &&
    !addPending;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">表关系</h3>
        <Button size="sm" variant="outline" disabled={scanPending} onClick={onScan}>
          {scanPending ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="size-3.5" aria-hidden />
          )}
          扫描表关联
        </Button>
      </div>
      <RelationsTable relations={relations} onDelete={onDelete} />
      <div className="space-y-2 rounded-lg border border-border p-3">
        {knowledge.isError && (
          <p className="text-xs text-destructive" role="alert">
            知识条目加载失败,无法选择关联表,请刷新重试
          </p>
        )}
        <div className="flex flex-wrap items-end gap-3">
          <TableSelect
            label="左表(知识条目)"
            ariaLabel="左表"
            value={srcTable}
            onChange={onSrcTableChange}
            q={knowledge}
            options={tableOptions}
            placeholder="选择左表"
          />
          <TableSelect
            label="右表(知识条目)"
            ariaLabel="右表"
            value={tgtTable}
            onChange={onTgtTableChange}
            q={knowledge}
            options={tableOptions}
            placeholder="选择右表"
          />
          <Button size="sm" disabled={!canAdd} onClick={onAdd}>
            <Plus className="size-3.5" aria-hidden />
            新增关系
          </Button>
        </div>
        {srcTable && tgtTable && (
          <JoinPairRows
            pairs={joinPairs}
            srcCols={columnsOf(srcTable)}
            tgtCols={columnsOf(tgtTable)}
            onPatch={onPatchPair}
            onRemove={onRemovePair}
            onAdd={onAddPair}
          />
        )}
      </div>
    </div>
  );
}
