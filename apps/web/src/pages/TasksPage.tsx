/* 展示任务列表和步骤状态，并提供暂停、恢复、取消与审批操作。 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { DataView, type DataViewColumn } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { PageHeader } from "@kongming/ui/page-header";
import { Pagination } from "@kongming/ui/pagination";
import { StatusBadge } from "@kongming/ui/status-badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { listTasks, TASK_STATUS_META } from "@kongming/api-client/task-api";
import type { TaskItem } from "@kongming/api-client/task-api";
import { useTenant } from "@kongming/api-client/tenant-store";

const PAGE_SIZE = 20;

export default function TasksPage() {
  const navigate = useNavigate();
  const tenant = useTenant((s) => s.current);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<string>("all");
  const statusParam = status === "all" ? undefined : status;
  const { data, isLoading, isError } = useQuery({
    queryKey: ["tasks", tenant, page, statusParam],
    queryFn: () => listTasks(page, PAGE_SIZE, statusParam),
  });

  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // 末页行被清空时钳回有效页(否则滞留空页且分页器已消失,无入口返回)
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const columns: DataViewColumn<TaskItem>[] = [
    {
      key: "goal",
      header: "目标",
      cardPriority: 1,
      render: (r) => <span className="font-medium text-foreground">{r.goal}</span>,
    },
    {
      key: "status",
      header: "状态",
      cardPriority: 2,
      render: (r) => <StatusBadge meta={TASK_STATUS_META} value={r.status} />,
    },
    {
      key: "updated_at",
      header: "更新时间",
      render: (r) => new Date(r.updated_at).toLocaleString(),
    },
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-4 py-6">
      <PageHeader
        title="任务"
        description="跨请求的持久任务:目标分解为步骤,证据驱动执行"
        actions={
          <StatusFilter
            value={status}
            onChange={(v) => {
              setStatus(v);
              setPage(1);
            }}
          />
        }
      />
      {isError && <ErrorAlert />}
      <DataView
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(r) => r.id}
        isLoading={isLoading}
        isEmpty={!isLoading && !isError && total === 0}
        emptyText="还没有任务。"
        actions={[{ label: "查看", onSelect: (r) => void navigate(`/tasks/${r.id}`) }]}
      />
      {total > PAGE_SIZE && (
        <Pagination page={page} pageCount={pageCount} total={total} onPageChange={setPage} />
      )}
    </div>
  );
}

/** 提供“全部”和各任务状态筛选,选择后立即查询。 */
function StatusFilter(props: { value: string; onChange: (v: string) => void }) {
  return (
    <Select value={props.value} onValueChange={props.onChange}>
      <SelectTrigger aria-label="按状态筛选任务" className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">全部状态</SelectItem>
        {Object.entries(TASK_STATUS_META).map(([value, meta]) => (
          <SelectItem key={value} value={value}>
            {meta.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
