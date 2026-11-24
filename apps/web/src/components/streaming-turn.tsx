/* 流式事件只更新本组件状态，避免整页随每个事件重渲染；中止或切换上下文后忽略迟到事件。 */

import { useEffect, useRef, useState } from "react";

import { StreamCard, mergeToolCall } from "@/components/stream-card";
import {
  streamChatQuery,
  type ChatQueryOut,
  type StreamStageOut,
  type StreamToolCallOut,
} from "@/lib/api";

interface StreamingTurnProps {
  /** 发起时冻结的请求体(单次生效,不随页面重渲染变更) */
  body: unknown;
  signal: AbortSignal;
  /** 终态回调:result 到达(乐观快照接管) */
  onResult: (out: ChatQueryOut) => void;
  /** 终态回调:error 事件或传输层失败(已过滤主动 abort) */
  onError: (message: string) => void;
  /** 响应头到达时上报本轮标识(X-Trace-Id) */
  onTraceId: (traceId: string) => void;
}

export function StreamingTurn({ body, signal, onResult, onError, onTraceId }: StreamingTurnProps) {
  const [stages, setStages] = useState<StreamStageOut[]>([]);
  const [sql, setSql] = useState<string | null>(null);
  const [summary, setSummary] = useState("");
  const [toolCalls, setToolCalls] = useState<StreamToolCallOut[]>([]);
  // 回调进 ref:effect 单次发起([]),调用时取最新渲染周期闭包,防陈旧捕获
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const onTraceIdRef = useRef(onTraceId);
  onTraceIdRef.current = onTraceId;
  // 存活标志提为 ref:开发模式严格模式会把挂载效应跑两遍(挂→卸→挂),
  // 若用闭包变量,第二次挂载会再发一次请求——同一问句跑两轮模型、双倍 token;
  // 提为 ref 后清理与二次挂载共享同一标志,请求仍只发一次(fired 门禁),
  // 二次挂载把标志置活,唯一请求的回调继续生效。
  const aliveRef = useRef(true);
  const firedRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    if (!firedRef.current) {
      firedRef.current = true;
      void (async () => {
        try {
          await streamChatQuery(
            body,
            {
              onStage: (d) => {
                if (aliveRef.current) setStages((prev) => [...prev, d]);
              },
              onSql: (d) => {
                if (aliveRef.current) setSql(d.sql);
              },
              onSummary: (d) => {
                if (aliveRef.current) setSummary((prev) => prev + d.delta);
              },
              onToolCall: (d) => {
                if (aliveRef.current) setToolCalls((prev) => mergeToolCall(prev, d));
              },
              onResult: (out) => {
                if (aliveRef.current) onResultRef.current(out);
              },
              onError: (d) => {
                if (aliveRef.current) onErrorRef.current(d.error);
              },
              onTraceId: (traceId) => {
                if (aliveRef.current && traceId) onTraceIdRef.current(traceId);
              },
            },
            signal,
          );
        } catch (err) {
          if (signal.aborted) return; // 主动取消:静默(呈现由取消/停止入口清理)
          if (aliveRef.current) {
            onErrorRef.current(err instanceof Error ? err.message : "问答服务异常");
          }
        }
      })();
    }
    return () => {
      aliveRef.current = false;
    };
    // body/signal 发起时即冻结;回调经 ref 取最新——重渲染不重订(单次发起语义)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <StreamCard stages={stages} sql={sql} summary={summary} toolCalls={toolCalls} />;
}
