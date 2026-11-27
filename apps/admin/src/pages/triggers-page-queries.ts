/* 触发器页的查询装配:列表、表单候选(工作流/助手/数据源)与执行历史。 */

import { useQuery } from "@tanstack/react-query";

import { api } from "@kongming/api-client/api";
import { datasourceApi } from "@kongming/api-client/datasource-api";
import {
  cronPreview,
  listTriggers,
  pageTriggerRuns,
  type TriggerItem,
} from "@kongming/api-client/trigger-api";
import { listWorkflows } from "@kongming/api-client/workflow-api";
import { useDebounced } from "@kongming/ui/use-debounced";
import { PAGE_SIZE, type FormState } from "./triggers-page-model";

export function useTriggersList() {
  return useQuery({ queryKey: ["admin", "triggers"], queryFn: listTriggers });
}

// 目标候选(封闭集合,按类型联动;自由输入拼错会挂到运行期才暴露)。
// 弹窗打开时才拉取,候选随 form.targetType 切换取用。
export function useTriggerTargetQueries(form: FormState | null) {
  const workflows = useQuery({
    queryKey: ["admin", "workflow"],
    queryFn: listWorkflows,
    enabled: form !== null,
  });
  const bots = useQuery({
    queryKey: ["bots"],
    queryFn: () => api.get<{ bot_key: string; name: string }[]>("/system/bots"),
    enabled: form !== null,
  });
  return { workflows, bots };
}

// cron 预览使用 400ms 防抖;空表达式或弹窗关闭时不发请求。
export function useCronPreview(form: FormState | null) {
  const debounced = useDebounced(form?.cronExpr ?? "", 400);
  const cronFires = useQuery({
    queryKey: ["admin", "cron-preview", debounced],
    queryFn: () => cronPreview(debounced),
    enabled: form?.type === "CRON" && debounced.trim() !== "",
  });
  return { cronExprDebounced: debounced, cronFires };
}

// 数据任务目标的数据源候选,仅目标类型为 task 时拉取
export function useTriggerDatasources(form: FormState | null) {
  return useQuery({
    queryKey: ["admin", "trigger-task-datasources"],
    queryFn: datasourceApi.options,
    enabled: form?.targetType === "task",
  });
}

export function useTriggerRuns(historyOf: TriggerItem | null, page: number) {
  return useQuery({
    queryKey: ["admin", "trigger-runs", historyOf?.id, page],
    queryFn: () => pageTriggerRuns(historyOf!.id, page, PAGE_SIZE),
    enabled: historyOf !== null,
  });
}
