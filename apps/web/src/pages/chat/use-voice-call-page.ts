/* 页面级通话接线:能力探测决定入口、停止确认播放、新问顶掉旧轮、以听写原话+关联字段交接既有问答。
   ChatPage 只消费状态与入口回调,媒体面细节在 use-voice-call。 */

import { useEffect, useRef, useState } from "react";
import { getVoiceCapability } from "@/lib/api";
import { STOP_REPLY } from "@/lib/voice-call";
import { useVoiceCall } from "./use-voice-call";

interface UseVoiceCallPageOptions {
  tenantId: string | null;
  /** 既有流式问答入口(听写原话 + voice_turn_token 走同一链路,5.3)。 */
  submitTurn: (question: string, voiceTurnToken: string) => void;
  /** 既有取消入口(新问顶掉旧轮:旧轮收敛为已取消,语音场景不回填输入框)。 */
  stopStreaming: (options?: { restoreDraft?: boolean }) => void;
  /** 上一轮画面仍在流式(决定是否需要先取消)。 */
  streaming: boolean;
}

export function useVoiceCallPage(options: UseVoiceCallPageOptions) {
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const localAudioRef = useRef<HTMLAudioElement | null>(null);
  const [muted] = useState(false);
  // 部署未配置实时语音模型时入口置灰;null=探测未完成,暂置灰防闪动
  const [voiceEnabled, setVoiceEnabled] = useState<boolean | null>(null);

  const stopLocalReply = () => {
    localAudioRef.current?.pause();
    if (localAudioRef.current) localAudioRef.current.currentTime = 0;
  };

  const playLocalReply = (src: string | undefined) => {
    stopLocalReply();
    if (!src) return;
    try {
      const audio = new Audio(src);
      localAudioRef.current = audio;
      void audio.play()?.catch(() => undefined);
    } catch {
      // 音频播放不可用时不阻断问答发起
    }
  };

  const {
    phase,
    failure,
    start: startCall,
    hangUp: hangUpCall,
    sampleAudioLevels,
  } = useVoiceCall({
    tenantId: options.tenantId,
    onTurnRequest: (turn) => {
      const current = optionsRef.current;
      if (current.streaming) current.stopStreaming({ restoreDraft: false });
      // 静默进入查询:不播开场白,避免每轮固定寒暄;查询进度由通话浮层状态呈现
      current.submitTurn(turn.question, turn.token);
    },
    onSpeechStarted: () => {
      // 即时打断感:立即停播本地应答并静音远端(hook 内处理),不标为麦克风「已静音」以免误导用户与冻结音量小球
      stopLocalReply();
    },
    onPlaybackStarted: () => {
      // 模型正式回答起播时立即收掉未念完的本地应答,避免两段声音叠音
      stopLocalReply();
    },
    onTurnStopped: () => {
      // 后端停止工具已取消在途问答并下发 turn.stopped;这里只播确认,
      // 不再调 stopStreaming,避免第二次取消和打断通话
      playLocalReply(STOP_REPLY);
    },
    onEnded: () => {
      stopLocalReply();
    },
  });

  useEffect(() => {
    return () => {
      localAudioRef.current?.pause();
    };
  }, []);

  // 能力探测:未启用的部署入口置灰;探测失败同样置灰(刷新恢复),由建立失败的
  // 未启用分类兜底置灰(探测谎报或配置被热改)
  useEffect(() => {
    let alive = true;
    getVoiceCapability()
      .then((c) => {
        if (alive) setVoiceEnabled(c.enabled);
      })
      .catch(() => {
        if (alive) setVoiceEnabled(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 建立失败且原因确为未启用:入口置灰
  useEffect(() => {
    if (failure === "not_enabled") setVoiceEnabled(false);
  }, [failure]);

  const start = (): void => {
    void startCall();
  };

  const hangUp = (): void => {
    void hangUpCall();
  };

  return { phase, failure, muted, voiceEnabled, start, hangUp, sampleAudioLevels };
}
