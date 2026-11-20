/* 问答口径依据面板：列出命中的指标名、口径表达式和修订时间。 */

import { useNavigate } from "react-router";
import { ChevronRight, Ruler } from "lucide-react";

import { formatDateTime } from "@/lib/datetime";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

type MetricHit = {
  metric_id: string;
  metric_name: string;
  sql_template: string;
  updated_at?: string | null | undefined;
};

/* 展示指标名称、口径表达式和修订时间；管理链接由路由权限守卫处理。 */
export function MetricProvenancePanel({ hits }: { hits: MetricHit[] }) {
  const navigate = useNavigate();
  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
          aria-hidden
        />
        <Ruler className="size-3.5" aria-hidden />
        口径依据({hits.length})
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-1.5 space-y-2 rounded-md border border-border bg-muted/40 p-3">
          {hits.map((h) => (
            <li key={h.metric_id || h.metric_name} className="space-y-0.5">
              <Button
                variant="link"
                size="xs"
                className="h-auto p-0 text-xs font-medium"
                onClick={() => {
                  void navigate("/admin/semantic");
                }}
              >
                {h.metric_name}
              </Button>
              <p className="font-mono text-xs text-muted-foreground">{h.sql_template}</p>
              {h.updated_at && (
                <p className="text-2xs text-muted-foreground">
                  修订于 {formatDateTime(h.updated_at)}
                </p>
              )}
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}
