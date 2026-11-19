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
}

export function StreamingTurn({ body, signal, onResult, onError }: StreamingTurnProps) {
  const [stages, setStages] = useState<StreamStageOut[]>([]);
  const [sql, setSql] = useState<string | null>(null);
  const [summary, setSummary] = useState("");
  const [toolCalls, setToolCalls] = useState<StreamToolCallOut[]>([]);
  // 回调进 ref:effect 单次发起([]),调用时取最新渲染周期闭包,防陈旧捕获
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        await streamChatQuery(
          body,
          {
            onStage: (d) => {
              if (alive) setStages((prev) => [...prev, d]);
            },
            onSql: (d) => {
              if (alive) setSql(d.sql);
            },
            onSummary: (d) => {
              if (alive) setSummary((prev) => prev + d.delta);
            },
            onToolCall: (d) => {
              if (alive) setToolCalls((prev) => mergeToolCall(prev, d));
            },
            onResult: (out) => {
              if (alive) onResultRef.current(out);
            },
            onError: (d) => {
              if (alive) onErrorRef.current(d.error);
            },
          },
          signal,
        );
      } catch (err) {
        if (signal.aborted) return; // 主动取消:静默(呈现由取消/停止入口清理)
        if (alive) onErrorRef.current(err instanceof Error ? err.message : "问答服务异常");
      }
    })();
    return () => {
      alive = false;
    };
    // body/signal 发起时即冻结;回调经 ref 取最新——重渲染不重订(单次发起语义)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <StreamCard stages={stages} sql={sql} summary={summary} toolCalls={toolCalls} />;
}
