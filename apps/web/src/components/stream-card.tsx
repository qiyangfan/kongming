/* 流式过程卡片(017 query-streaming,web-nav spec「对话区渲染」):
   阶段指示按节点完成顺序逐个点亮(running=转圈/primary,done=勾/muted;
   selfheal 显示「第 N 次自愈重试」)+ SQL 折叠卡提前渲染(sql 事件)+
   summary 打字机式逐事件追加;终态由 AssistantCard 接管,本组件随之卸载。
   019:tool_call 事件渲染工具调用链(chips:工具名 + 状态图标 + 结果摘要,
   ReAct 内核节点名 react/tool_dispatch 不在 8 节点标签表内,原样显示)。 */

import { Check, ChevronRight, Database, Loader2, Wrench, X } from "lucide-react";

import type { StreamStageOut, StreamToolCallOut } from "@/lib/api";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

const STAGE_LABELS: Record<string, string> = {
  understand: "理解问题",
  retrieve: "检索表结构",
  generate: "生成 SQL",
  validate: "校验",
  execute: "执行",
  selfheal: "自愈重试",
  summarize: "总结",
  doc_answer: "知识问答", // 024
  // 019 ReAct 内核节点
  react: "思考与决策",
  tool_dispatch: "工具执行",
  finalize: "组织答案",
};

function stageLabel(stage: StreamStageOut): string {
  if (stage.node === "selfheal" && stage.retry_count > 0) {
    return `第 ${stage.retry_count} 次自愈重试`;
  }
  return STAGE_LABELS[stage.node] ?? stage.node;
}

/** 工具调用链 chips(019):同一工具的 running→done/rejected 合并为一条(按序更新) */
export function mergeToolCall(
  list: StreamToolCallOut[],
  next: StreamToolCallOut,
): StreamToolCallOut[] {
  // running 帧追加;终态帧回填最近一条同名 running(无匹配则追加)
  if (next.status === "running") return [...list, next];
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].tool_name === next.tool_name && list[i].status === "running") {
      return [...list.slice(0, i), next, ...list.slice(i + 1)];
    }
  }
  return [...list, next];
}

export interface StreamCardProps {
  stages: StreamStageOut[];
  sql: string | null;
  summary: string;
  toolCalls?: StreamToolCallOut[];
}

export function StreamCard({ stages, sql, summary, toolCalls = [] }: StreamCardProps) {
  return (
    <div className="space-y-3" role="status" aria-live="polite">
      {stages.length > 0 && (
        <ol className="flex flex-wrap items-center gap-x-3 gap-y-1.5" aria-label="执行进度">
          {stages.map((s, i) => (
            <li
              key={`${s.node}-${i}`}
              className="flex items-center gap-1.5 text-xs"
              aria-label={stageLabel(s)}
            >
              {s.status === "running" ? (
                <Loader2 className="size-3.5 animate-spin text-primary" aria-hidden />
              ) : (
                <Check className="size-3.5 text-muted-foreground" aria-hidden />
              )}
              <span
                className={
                  s.status === "running"
                    ? "font-medium text-primary"
                    : "text-muted-foreground"
                }
              >
                {stageLabel(s)}
              </span>
            </li>
          ))}
        </ol>
      )}

      {toolCalls.length > 0 && (
        <ol className="space-y-1" aria-label="工具调用链">
          {toolCalls.map((t, i) => (
            <li
              key={`${t.tool_name}-${i}`}
              className="flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2.5 py-1.5 text-xs"
            >
              {t.status === "running" ? (
                <Loader2 className="size-3.5 animate-spin text-primary" aria-hidden />
              ) : t.status === "done" ? (
                <Check className="size-3.5 text-primary" aria-hidden />
              ) : (
                <X className="size-3.5 text-destructive" aria-hidden />
              )}
              <Wrench className="size-3.5 text-muted-foreground" aria-hidden />
              <span className="font-medium text-foreground">{t.tool_name}</span>
              {t.result_digest && (
                <span className="truncate text-muted-foreground">{t.result_digest}</span>
              )}
            </li>
          ))}
        </ol>
      )}

      {sql && (
        <Collapsible defaultOpen>
          <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ChevronRight
              className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
              aria-hidden
            />
            <Database className="size-3.5" aria-hidden />
            查看 SQL
          </CollapsibleTrigger>
          <CollapsibleContent>
            <pre className="mt-1.5 overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed text-foreground">
              {sql}
            </pre>
          </CollapsibleContent>
        </Collapsible>
      )}

      {summary && (
        <p className="px-1 text-[15px] leading-relaxed text-foreground">{summary}</p>
      )}
    </div>
  );
}
