/* 数据源列表与删除确认对话框。 */

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { StatusSwitch } from "@/components/status-switch";
import { DataView } from "@/components/data-view";
import type { DatasourceItemOut } from "@/lib/datasource-api";

interface DatasourcesTableProps {
  rows: DatasourceItemOut[];
  isPending: boolean;
  togglePending: boolean;
  toggleId: string | null;
  toggleError: string;
  onToggle: (item: DatasourceItemOut) => void;
  onEdit: (item: DatasourceItemOut) => void;
  onDelete: (item: DatasourceItemOut) => void;
}

/** 数据源表:键/名称/租户/连接/默认 schema/启停,行操作编辑与删除。 */
export function DatasourcesTable(props: DatasourcesTableProps) {
  const { rows, isPending, togglePending, toggleId, toggleError } = props;
  return (
    <DataView
      rows={rows}
      rowKey={(d) => d.id}
      isLoading={isPending}
      isEmpty={!isPending && rows.length === 0}
      emptyText="暂无数据源"
      columns={[
        {
          key: "datasource_key",
          header: "键",
          cardPriority: 1,
          render: (d) => (
            <span className="block max-w-32 truncate font-medium" title={d.datasource_key}>
              {d.datasource_key}
            </span>
          ),
        },
        {
          key: "name",
          header: "名称",
          cardPriority: 2,
          render: (d) => (
            <span className="block max-w-32 truncate" title={d.name}>
              {d.name}
            </span>
          ),
        },
        {
          key: "tenant_name",
          header: "归属租户",
          cardPriority: 3,
          render: (d) => (
            <span className="block max-w-40 truncate" title={`${d.tenant_name}(${d.tenant_key})`}>
              {d.tenant_name}({d.tenant_key})
            </span>
          ),
        },
        {
          key: "host",
          header: "连接",
          cardPriority: 2, // 连接标识(host:port/库)窄屏卡片可见
          cellClassName: "text-muted-foreground",
          render: (d) => (
            <span className="block max-w-56 truncate" title={`${d.host}:${d.port}/${d.database}`}>
              {d.host}:{d.port}/{d.database}
            </span>
          ),
        },
        {
          key: "default_schema",
          header: "默认 schema",
          render: (d) => d.default_schema,
        },
        {
          key: "status",
          header: "状态",
          cardPriority: 4,
          render: (d) => (
            <StatusSwitch
              active={d.status === "ACTIVE"}
              pending={togglePending}
              error={!togglePending && toggleId === d.id && toggleError ? toggleError : null}
              onToggle={() => props.onToggle(d)}
              ariaLabel={`${d.name} 启用状态`}
            />
          ),
        },
      ]}
      actions={[
        { label: "编辑", onSelect: props.onEdit },
        { label: "删除", destructive: true, onSelect: props.onDelete },
      ]}
    />
  );
}

interface DeleteDatasourceDialogProps {
  deleting: DatasourceItemOut | null;
  onClose: () => void;
  pending: boolean;
  error: Error | null;
  onConfirm: () => void;
}

/** 删除数据源确认:成功才关,失败保持开启并展示错误。 */
export function DeleteDatasourceDialog(props: DeleteDatasourceDialogProps) {
  return (
    <AlertDialog open={props.deleting !== null} onOpenChange={(open) => !open && props.onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除数据源 {props.deleting?.datasource_key}?</AlertDialogTitle>
          <AlertDialogDescription>
            删除立即生效且不可恢复:删除后,使用该数据源的问答将直接报错。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {props.error && (
          <p className="text-destructive text-sm" role="alert">
            {props.error.message}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          {/* 受控关闭:mutation 成功才关对话框,失败保持开启展示错误 */}
          <Button variant="destructive" onClick={props.onConfirm} disabled={props.pending}>
            {props.pending ? "删除中…" : "删除"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
