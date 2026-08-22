/* 流式过程卡片(017 query-streaming,web-nav spec「对话区渲染」):
   阶段指示按节点完成顺序逐个点亮(running=转圈/primary,done=勾/muted;
   selfheal 显示「第 N 次自愈重试」)+ SQL 折叠卡提前渲染(sql 事件)+
   summary 打字机式逐事件追加;终态由 AssistantCard 接管,本组件随之卸载。 */

import { Check, ChevronRight, Database, Loader2 } from "lucide-react";

import type { StreamStageOut } from "@/lib/api";
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
};

function stageLabel(stage: StreamStageOut): string {
  if (stage.node === "selfheal" && stage.retry_count > 0) {
    return `第 ${stage.retry_count} 次自愈重试`;
  }
  return STAGE_LABELS[stage.node] ?? stage.node;
}

export interface StreamCardProps {
  stages: StreamStageOut[];
  sql: string | null;
  summary: string;
}

export function StreamCard({ stages, sql, summary }: StreamCardProps) {
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
