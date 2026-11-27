/* 控制面状态与本地命令:本地命令不依赖锦官;跨服务命令终态看回执表。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  controlApi,
  type LocalCommandIn,
  type SharedSnapshotOut,
} from "@kongming/api-client/control-api";
import { formatDateTime } from "@kongming/api-client/datetime";
import type { components } from "@kongming/api-client/api-types.gen";
import { Badge } from "@kongming/ui/badge";
import { Button } from "@kongming/ui/button";
import { DataView, type DataViewColumn } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { Input } from "@kongming/ui/input";
import { PageHeader } from "@kongming/ui/page-header";
import { StatCard } from "@kongming/ui/stat-card";
import { Textarea } from "@kongming/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";

type Receipt = components["schemas"]["ReceiptItemOut"];
type Status = components["schemas"]["ControlStatusOut"];

const ACTIONS = [
  { value: "rag.reindex", label: "重建索引 (rag.reindex)" },
  { value: "cache.clear", label: "清缓存 (cache.clear)" },
  { value: "task.cancel", label: "取消任务 (task.cancel)" },
];

const receiptColumns: DataViewColumn<Receipt>[] = [
  {
    key: "action",
    header: "命令",
    cardPriority: 1,
    render: (r) => <span className="font-medium">{r.action}</span>,
  },
  {
    key: "status",
    header: "状态",
    cardPriority: 2,
    render: (r) => <Badge variant="outline">{r.status}</Badge>,
  },
  { key: "source", header: "来源", cardPriority: 3, render: (r) => r.source },
  { key: "actor", header: "操作者", render: (r) => r.actor || "—" },
  {
    key: "updated_at",
    header: "时间",
    cellClassName: "text-muted-foreground",
    render: (r) => formatDateTime(r.updated_at),
  },
];

function StatusSection({ status }: { status: Status | undefined }) {
  return (
    <div className="space-y-2">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="运行模式" value={status?.mode ?? "—"} />
        <StatCard
          label="快照版本"
          value={status?.snapshot_present ? String(status.snapshot_version) : "无"}
        />
        <StatCard label="待上报事件" value={status?.pending_usage ?? 0} />
        <StatCard label="丢弃事件" value={status?.dropped_usage ?? 0} />
      </div>
      <p className="text-sm text-muted-foreground">
        stale={String(status?.stale ?? true)} · security_ok=
        {String(status?.security_ok ?? false)}
        {status?.last_error ? ` · ${status.last_error}` : ""}
      </p>
    </div>
  );
}

function SharedControlSection({ snapshot }: { snapshot: SharedSnapshotOut | undefined }) {
  if (!snapshot?.present) {
    return (
      <section className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        共享控制只读:当前无有效快照(standalone 或控制面尚未同步)。
      </section>
    );
  }
  return (
    <section className="space-y-2 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-medium">共享控制(只读)</h3>
      <div className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <p>租户 {snapshot.tenants.length}</p>
        <p>权益 {snapshot.entitlements.length}</p>
        <p>策略包 {snapshot.policy_packages.length}</p>
        <p>目录条目 {snapshot.catalog.length}</p>
      </div>
      {snapshot.policy_packages.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {snapshot.policy_packages.map((p) => (
            <Badge key={p.package_key} variant="outline">
              {p.package_key}@{p.version}({p.status})
            </Badge>
          ))}
        </div>
      )}
      <p className="text-xs text-muted-foreground">变更在锦官控制台;此处只读。</p>
    </section>
  );
}

function LocalCommandSection() {
  const qc = useQueryClient();
  const [action, setAction] = useState("rag.reindex");
  const [tenantKey, setTenantKey] = useState("");
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const run = useMutation({
    mutationFn: (body: LocalCommandIn) => controlApi.runLocal(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["control-receipts"] });
    },
  });
  const needsDatasource = action !== "task.cancel";
  const params: Record<string, string> = needsDatasource
    ? { tenant_key: tenantKey, datasource_key: target }
    : { tenant_key: tenantKey, task_id: target };
  const canRun =
    !run.isPending && tenantKey.trim() !== "" && target.trim() !== "" && reason.trim() !== "";

  return (
    <section className="space-y-2 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-medium">本地运行命令</h3>
      <div className="flex flex-wrap items-end gap-2">
        <Select value={action} onValueChange={setAction}>
          <SelectTrigger className="w-64">
            <SelectValue placeholder="选择命令" />
          </SelectTrigger>
          <SelectContent>
            {ACTIONS.map((a) => (
              <SelectItem key={a.value} value={a.value}>
                {a.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={tenantKey}
          onChange={(e) => setTenantKey(e.target.value)}
          placeholder="租户 key(如 default)"
          className="w-48"
        />
        <Input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder={needsDatasource ? "数据源 key" : "任务 ID"}
          className="w-64"
        />
        <Textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="原因(必填,进审计)"
          className="min-w-64 flex-1"
        />
        <Button
          onClick={() => run.mutate({ action, action_version: 1, params, reason })}
          disabled={!canRun}
        >
          {run.isPending ? "执行中…" : "执行"}
        </Button>
      </div>
      {run.isError && <ErrorAlert text="命令执行失败,请稍后重试。" />}
      {run.data && (
        <p className="text-sm">
          结果:{run.data.status}
          {run.data.error ? ` · ${run.data.error}` : ""}
        </p>
      )}
    </section>
  );
}

function ReceiptsSection() {
  const receipts = useQuery({
    queryKey: ["control-receipts"],
    queryFn: () => controlApi.receipts(50),
  });
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">最近命令回执</h3>
      {receipts.isError && <ErrorAlert text="回执加载失败。" />}
      <DataView
        rows={receipts.data?.items ?? []}
        rowKey={(r) => r.idempotency_key}
        isLoading={receipts.isPending}
        isEmpty={!receipts.isPending && (receipts.data?.items.length ?? 0) === 0}
        emptyText="暂无命令回执"
        columns={receiptColumns}
      />
    </section>
  );
}

export default function ControlPage() {
  const status = useQuery({ queryKey: ["control-status"], queryFn: controlApi.status });
  const snapshot = useQuery({ queryKey: ["control-snapshot"], queryFn: controlApi.snapshot });
  return (
    <div className="space-y-4">
      <PageHeader
        title="控制面"
        description="快照/上报状态与本地运行命令;跨服务命令由锦官控制台提交,此处只显示回执。"
      />
      {status.isError && <ErrorAlert text="控制面状态加载失败。" />}
      <StatusSection status={status.data} />
      <SharedControlSection snapshot={snapshot.data} />
      <LocalCommandSection />
      <ReceiptsSection />
    </div>
  );
}
