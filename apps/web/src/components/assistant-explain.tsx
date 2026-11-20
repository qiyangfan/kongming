/* 执行计划查询入口与 PostgreSQL EXPLAIN 计划树的文本格式化。 */

import { useState } from "react";
import { ChevronRight, Database, Loader2 } from "lucide-react";

import { explainPlan } from "@/lib/chat-api";
import { cellText } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

/* 展开时按需加载执行计划；异常结构以 JSON 展示，客户端只传 message_id。 */
export function ExplainCollapsible({ messageId }: { messageId: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    if (text !== null || error !== null || loading) return; // 成功即缓存;失败见重试钮
    setLoading(true);
    try {
      const out = await explainPlan(messageId);
      if (out.success) {
        setText(renderPlan(out.plan));
      } else {
        setError(out.error ?? "执行计划获取失败,请稍后再试。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "执行计划获取失败,请稍后再试。");
    }
    setLoading(false);
  }

  function retry(): void {
    setError(null);
    void load();
  }

  return (
    <Collapsible
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) void load();
      }}
    >
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
          aria-hidden
        />
        <Database className="size-3.5" aria-hidden />
        执行计划
      </CollapsibleTrigger>
      <CollapsibleContent>
        {(() => {
          if (loading) {
            return (
              <p className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="size-3 animate-spin" aria-hidden />
                计划生成中…
              </p>
            );
          }
          if (error) {
            return (
              <p role="alert" className="mt-1.5 flex items-center gap-2 text-xs text-destructive">
                {error}
                <Button variant="ghost" size="xs" onClick={retry}>
                  重试
                </Button>
              </p>
            );
          }
          return (
            <pre className="mt-1.5 overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed text-foreground">
              {text}
            </pre>
          );
        })()}
      </CollapsibleContent>
    </Collapsible>
  );
}

/* 格式化 PostgreSQL EXPLAIN 的计划树、节点类型、成本、行数和关联表。 */
function renderPlan(plan: unknown): string {
  if (Array.isArray(plan) && plan.length > 0) {
    const first: unknown = plan[0];
    if (first && typeof first === "object") {
      const root = (first as Record<string, unknown>)["Plan"];
      if (root && typeof root === "object") {
        return formatPlanNode(root as Record<string, unknown>, 0);
      }
    }
  }
  return JSON.stringify(plan, null, 2);
}

function planNodeLine(node: Record<string, unknown>, depth: number): string {
  const parts = [cellText(node["Node Type"]) || "节点"];
  const cost = Number(node["Total Cost"]);
  if (Number.isFinite(cost)) parts.push(`cost=${cost.toFixed(2)}`); // 非数值不渲染 NaN
  const rows = node["Actual Rows"] ?? node["Plan Rows"];
  if (rows !== undefined) parts.push(`rows=${cellText(rows)}`);
  if (node["Relation Name"]) parts.push(`on ${cellText(node["Relation Name"])}`);
  return "  ".repeat(depth) + parts.join("  ");
}

function formatPlanNode(node: Record<string, unknown>, depth: number): string {
  const lines = [planNodeLine(node, depth)];
  const children = node["Plans"];
  if (Array.isArray(children)) {
    for (const c of children) {
      if (c && typeof c === "object") {
        lines.push(formatPlanNode(c as Record<string, unknown>, depth + 1));
      }
    }
  }
  return lines.join("\n");
}
