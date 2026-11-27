/* 用量页的查询、筛选与汇总计算;页面只负责组装。 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { usagePage, usageSummary, usageTimeline, usageTrend } from "@kongming/api-client/usage-api";
import { useDebounced } from "@kongming/ui/use-debounced";
import { systemApi } from "@kongming/api-client/system-api";

export const PAGE_SIZE = 20;

export function useUsagePageState() {
  const [days, setDays] = useState(30);
  const [model, setModel] = useState("");
  const [task, setTask] = useState("");
  const [status, setStatus] = useState("");
  const [traceId, setTraceId] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [protocol, setProtocol] = useState("");
  const [page, setPage] = useState(1);
  const [timelineTrace, setTimelineTrace] = useState<string | null>(null);

  // 防抖值驱动查询(输入即时回显,查询 300ms 收敛)
  const modelQ = useDebounced(model);
  const taskQ = useDebounced(task);
  const traceIdQ = useDebounced(traceId);

  const summary = useQuery({
    queryKey: ["usage", "summary"],
    queryFn: () => usageSummary(),
  });
  const trend = useQuery({
    queryKey: ["usage", "trend", days],
    queryFn: () => usageTrend(days),
  });
  const tenants = useQuery({
    queryKey: ["admin", "tenants"],
    queryFn: systemApi.listTenants,
  });
  const list = useQuery({
    queryKey: ["usage", "page", page, modelQ, taskQ, status, traceIdQ, tenantId, protocol],
    queryFn: () =>
      usagePage({
        page,
        size: PAGE_SIZE,
        ...(modelQ ? { model: modelQ } : {}),
        ...(taskQ ? { task: taskQ } : {}),
        ...(status ? { status: status as "SUCCESS" | "ERROR" } : {}),
        ...(traceIdQ ? { trace_id: traceIdQ } : {}),
        ...(tenantId ? { tenant_id: tenantId } : {}),
        ...(protocol ? { protocol } : {}),
      }),
  });
  const timeline = useQuery({
    queryKey: ["usage", "timeline", timelineTrace],
    queryFn: () => usageTimeline(timelineTrace!),
    enabled: timelineTrace !== null,
  });

  // 筛选结果变少后重新计算页码，避免显示空页。
  // 筛选 onChange 已同步 setPage(1)(防抖值到达时 page 已归 1,
  // 单请求无「旧页一次+页 1 一次」双发窗口),此处不再挂回页 effect。
  const { data: listData } = list;
  useEffect(() => {
    if (listData && listData.rows.length === 0 && listData.total > 0 && page > 1) {
      setPage((p) => Math.max(1, p - 1));
    }
  }, [listData, page]);

  // 模型/任务筛选项取自已加载的汇总数据,避免额外请求。
  const modelOptions = useMemo(
    () =>
      Array.from(new Set((summary.data?.items ?? []).map((i) => i.model))).sort((a, b) =>
        a.localeCompare(b),
      ),
    [summary.data],
  );
  const taskOptions = useMemo(
    () =>
      Array.from(new Set((summary.data?.items ?? []).map((i) => i.task))).sort((a, b) =>
        a.localeCompare(b),
      ),
    [summary.data],
  );

  const totals = useMemo(() => computeTotals(summary.data?.items ?? []), [summary.data]);

  return {
    days,
    setDays,
    model,
    setModel,
    task,
    setTask,
    status,
    setStatus,
    traceId,
    setTraceId,
    tenantId,
    setTenantId,
    protocol,
    setProtocol,
    page,
    setPage,
    timelineTrace,
    setTimelineTrace,
    summary,
    trend,
    tenants,
    list,
    timeline,
    modelOptions,
    taskOptions,
    totals,
  };
}

type SummaryItems = NonNullable<Awaited<ReturnType<typeof usageSummary>>["items"]>;

/** 汇总卡口径:调用数/失败数/总量求和;延迟取各组平均的未加权平均(近似值)。 */
function computeTotals(items: SummaryItems) {
  const calls = items.reduce((a, i) => a + i.calls, 0);
  const errors = items.reduce((a, i) => a + i.calls_error, 0);
  const tokens = items.reduce((a, i) => a + i.total_tokens, 0);
  const latAvg: number[] = [];
  for (const i of items) {
    if (typeof i.latency_ms_avg === "number") {
      // 分组数不均,取组平均的近似口径(展示卡用途足够,精确值以明细行为准)
      latAvg.push(i.latency_ms_avg);
    }
  }
  return {
    calls,
    errors,
    tokens,
    errorRate: calls > 0 ? errors / calls : 0,
    latAvg: latAvg.length ? Math.round(latAvg.reduce((a, v) => a + v, 0) / latAvg.length) : null,
  };
}
