/* 语音输入：录音结束后转写并回填草稿；上下文切换或取消时作废在途回调。 */

import { useCallback, useEffect, useRef, useState } from "react";

import { transcribeAudio } from "@/lib/chat-api";

export interface RecordingApi {
  recording: boolean;
  transcribing: boolean;
  cancelRecording: () => void;
  onMicClick: () => Promise<void>;
}

/** 麦克风录音 + 转写；结果经 setDraft 回填，错误经 onError 呈现。 */
export function useRecording(
  setDraft: React.Dispatch<React.SetStateAction<string>>,
  onError: (message: string | null) => void,
): RecordingApi {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recGenRef = useRef(0); // 录音/转写代际:cancelRecording 自增作废在途回调

  const cancelRecording = useCallback((): void => {
    recGenRef.current += 1; // 在途转写/尾块回调随之失效(不落新上下文的草稿)
    recorderRef.current?.stream.getTracks().forEach((t) => t.stop());
    recorderRef.current = null;
    chunksRef.current = [];
    setRecording(false);
    setTranscribing(false);
  }, []);

  /** 开始/停止录音；停止后由 onstop 收尾并转写。 */
  async function onMicClick(): Promise<void> {
    if (recording) {
      recorderRef.current?.stop(); // onstop 负责收尾+转写
      return;
    }
    onError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // 代际守卫:每次开始录音自增;取消/上下文切换后旧代际的 dataavailable/
      // onstop/转写回调全部失效——防止 track 停止时浏览器 flush 的尾块与
      // 在途转写结果写进切换后的新上下文输入框
      const gen = ++recGenRef.current;
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (recGenRef.current === gen && e.data.size) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        if (recGenRef.current !== gen) {
          stream.getTracks().forEach((t) => t.stop()); // 已取消:只回收硬件,不再落笔
          return;
        }
        stream.getTracks().forEach((t) => t.stop());
        recorderRef.current = null;
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        if (blob.size < 1024) return; // 近乎无声的误触
        setTranscribing(true);
        transcribeAudio(blob)
          .then((text) => {
            if (recGenRef.current !== gen) return; // 转写期间上下文已切换:丢弃
            if (!text) {
              onError("没有听清,请再试一次。");
              return;
            }
            setDraft((prev) => (prev ? `${prev} ${text}` : text));
          })
          .catch((err) => {
            if (recGenRef.current !== gen) return;
            onError(err instanceof Error ? err.message : "语音识别未成,请稍后再试。");
          })
          .finally(() => {
            if (recGenRef.current === gen) setTranscribing(false);
          });
      };
      rec.start();
      recorderRef.current = rec;
      setRecording(true);
    } catch {
      onError("无法访问麦克风,请检查浏览器权限。");
    }
  }

  useEffect(() => {
    if (!recording) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancelRecording();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [recording, cancelRecording]);

  return { recording, transcribing, cancelRecording, onMicClick };
}
