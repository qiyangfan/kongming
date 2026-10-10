/* 流式过程卡片(017 query-streaming,web-nav spec「对话区渲染」):
   纵向跑马灯时间轴:阶段沿左侧轨道渐进点亮(running=呼吸光环+文字流光,
   done=实心点渐隐降权),容器限高,新节点入场平滑上滚,老节点经顶部
   mask 渐隐淡出(未超限不启用遮罩);工具调用明细以缩进子节点挂同一轴尾
   (019,running→done/rejected 同名合并);末节点 running 时轨道以渐隐线
   延伸,暗示链路仍在生长。
   SQL 折叠卡提前渲染(sql 事件)+ summary 打字机式逐事件追加;
   终态由 AssistantCard 接管,本组件随之卸载。
   ReAct 内核节点名 react/tool_dispatch 不在 8 节点标签表内,原样显示。 */

import { useEffect, useRef, useState } from "react";
import { ChevronRight, Database, Wrench, X } from "lucide-react";

import type { StreamStageOut, StreamToolCallOut } from "@/lib/api";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

const STAGE_LABELS: Record<string, string> = {
  understand: "理解问题",
  route_intent: "意图路由", // 093 三态意图路由(检索前)
  retrieve: "检索表结构",
  generate: "生成 SQL",
  validate: "校验",
  execute: "执行",
  selfheal: "自愈重试",
  summarize: "总结",
  doc_answer: "知识问答", // 024
  fused_answer: "融合作答", // 093 融合问答(数据结论 × 文档口径)
  // 019 ReAct 内核节点
  react: "思考与决策",
  tool_dispatch: "工具执行",
  finalize: "组织答案",
};

/** selfheal 轮次号对齐:后端 running 帧带进入时旧值(N-1),done 帧带完成后新值 N
    (nodes.py selfheal 返回 retry_count+1),不对齐则两帧各占一行且伪 running 永转圈。
    预算耗尽 done 帧沿用旧值(该轮未发生),标签毫秒级回指上一轮,可接受 */
function selfhealRound(s: StreamStageOut): number {
  return s.status === "running" ? s.retry_count + 1 : s.retry_count;
}

function stageLabel(stage: StreamStageOut): string {
  if (stage.node === "selfheal") {
    return `第 ${selfhealRound(stage)} 次自愈重试`;
  }
  return STAGE_LABELS[stage.node] ?? stage.node;
}

/** 工具调用链(019):同一工具的 running→done/rejected 合并为一条(按序更新) */
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

/** 阶段折叠:同一节点 running→done 两帧合并取最新(selfheal 按 retry_count 分行)。
    后端 stage_wrapper 对每节点对发 running/done,纯 append 渲染会重复成对出现 */
function mergeStages(list: StreamStageOut[]): StreamStageOut[] {
  const keyOf = (s: StreamStageOut) =>
    s.node === "selfheal" ? `selfheal-${selfhealRound(s)}` : s.node;
  const out: StreamStageOut[] = [];
  const index = new Map<string, number>();
  for (const s of list) {
    const key = keyOf(s);
    const i = index.get(key);
    if (i === undefined) {
      index.set(key, out.length);
      out.push(s);
    } else {
      out[i] = s;
    }
  }
  return out;
}

export interface StreamCardProps {
  stages: StreamStageOut[];
  sql: string | null;
  summary: string;
  toolCalls?: StreamToolCallOut[];
}

/** 轴上圆点:done 实心/running 叠呼吸光环/rejected 红;工具子节点小一档仍居轴心 */
function AxisDot({ status, small = false }: { status: string; small?: boolean }) {
  const size = small ? "size-2" : "size-3";
  const color =
    status === "rejected"
      ? "bg-destructive"
      : status === "done" && small
        ? "bg-primary/70"
        : "bg-primary";
  return (
    <span className={`relative flex shrink-0 items-center justify-center ${size}`}>
      {status === "running" && (
        <span
          className={`absolute inset-0 rounded-full bg-primary/30 animate-timeline-ping ${size}`}
          aria-hidden
        />
      )}
      <span className={`relative rounded-full ${size} ${color}`} aria-hidden />
    </span>
  );
}

/** 轨道段:接续上一节点到当前节点;末项 running 时以渐隐线暗示链路生长中 */
function Track({ tail, running }: { tail: boolean; running: boolean }) {
  if (tail && !running) return null;
  return (
    <span
      aria-hidden
      className={`absolute top-4 bottom-0 left-1.5 w-px -translate-x-1/2 ${
        tail && running ? "bg-gradient-to-b from-border to-transparent" : "bg-border"
      }`}
    />
  );
}

export function StreamCard({ stages, sql, summary, toolCalls = [] }: StreamCardProps) {
  const mergedStages = mergeStages(stages);
  const hasTimeline = mergedStages.length > 0 || toolCalls.length > 0;
  const listRef = useRef<HTMLOListElement>(null);
  const [clipped, setClipped] = useState(false);
  const timelineCount = mergedStages.length + toolCalls.length;
  // 滚动锚定签名:节点数或 running 集变化才重新校准(纯内容帧不触发,防 smooth
  // 滚动反复重启;running↔done 迁移签名必变,会触发校准但 delta=0 时不滚动)
  const anchorKey = `${timelineCount}:${mergedStages
    .map((s) => (s.status === "running" ? s.node : ""))
    .join(",")}:${toolCalls.map((t) => (t.status === "running" ? t.tool_name : "")).join(",")}`;

  // 跑马灯:以 running 节点为锚滚动——最后的 running 完整入窗(最新活动焦点,
  // 绝不被挤出可视区),首个 running 不落入顶部渐隐区;均已满足则不滚动。
  // 无 running(流尾)贴底。内容未超限不启用遮罩,首行不被误遮;
  // 时间轴卸载(流结束/新提问清空)时归零遮罩态,防重挂载首帧带残 mask。
  useEffect(() => {
    const el = listRef.current;
    if (!el) {
      setClipped(false);
      return;
    }
    setClipped(el.scrollHeight > el.clientHeight + 4);
    const MASK = 24; // 与顶部渐隐遮罩 24px 同源
    const runnings = el.querySelectorAll("[data-running]");
    if (runnings.length === 0) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      return;
    }
    const box = el.getBoundingClientRect();
    const first = runnings[0].getBoundingClientRect();
    const last = runnings[runnings.length - 1].getBoundingClientRect();
    let delta = 0;
    if (last.bottom > box.bottom) delta = last.bottom - box.bottom;
    else if (first.top < box.top + MASK) delta = first.top - (box.top + MASK);
    if (delta !== 0) el.scrollTo({ top: el.scrollTop + delta, behavior: "smooth" });
  }, [anchorKey]);

  return (
    <div className="space-y-3" role="status" aria-live="polite">
      {hasTimeline && (
        <ol
          ref={listRef}
          aria-label="执行进度"
          className={`relative max-h-40 overflow-hidden ${
            clipped
              ? "[-webkit-mask-image:linear-gradient(to_bottom,transparent,black_24px)] [mask-image:linear-gradient(to_bottom,transparent,black_24px)]"
              : ""
          }`}
        >
          {mergedStages.map((s, i) => {
            const tail = i === mergedStages.length - 1 && toolCalls.length === 0;
            const running = s.status === "running";
            return (
              <li
                key={`stage-${s.node}-${i}`}
                data-running={running || undefined}
                className={`relative flex items-start gap-3 pb-4 transition-opacity animate-in fade-in slide-in-from-left-2 duration-500 ${
                  running ? "opacity-100" : "opacity-50"
                }`}
                aria-label={stageLabel(s)}
              >
                <Track tail={tail} running={running} />
                <AxisDot status={s.status} />
                <span
                  className={`mt-1 text-xs ${
                    running
                      ? "animate-shimmer bg-gradient-to-r from-muted-foreground/50 via-foreground to-muted-foreground/50 bg-[length:200%_100%] bg-clip-text font-medium text-transparent"
                      : "text-muted-foreground"
                  }`}
                >
                  {stageLabel(s)}
                </span>
              </li>
            );
          })}

          {toolCalls.map((t, i) => {
            const tail = i === toolCalls.length - 1;
            const running = t.status === "running";
            return (
              <li
                key={`tool-${t.tool_name}-${i}`}
                data-running={running || undefined}
                className={`relative flex items-start gap-3 pb-3 pl-4 transition-opacity animate-in fade-in slide-in-from-left-2 duration-500 ${
                  running ? "opacity-100" : "opacity-50"
                }`}
                aria-label={t.tool_name}
              >
                <Track tail={tail} running={running} />
                <span className="ml-0.5 mt-1">
                  <AxisDot status={t.status} small />
                </span>
                <span className="flex min-w-0 items-center gap-1.5">
                  <Wrench className="size-3 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="truncate font-mono text-2xs tracking-tight text-foreground">
                    {t.tool_name}
                  </span>
                  {t.status === "rejected" && (
                    <X className="size-3 shrink-0 text-destructive" aria-hidden />
                  )}
                  {t.result_digest && (
                    <span className="truncate text-2xs text-muted-foreground">
                      {t.result_digest}
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      )}

      {sql && (
        <Collapsible defaultOpen className="animate-in fade-in slide-in-from-bottom-1 duration-500">
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
        <p className="animate-in fade-in px-1 text-md leading-relaxed text-foreground">{summary}</p>
      )}
    </div>
  );
}
