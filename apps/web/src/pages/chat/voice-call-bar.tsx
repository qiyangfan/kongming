/* 通话浮层与失败提示:通话中展示声音驱动的 Siri 式呼吸小球与状态文案(AnalyserNode RMS
   rAF 驱动),失败时按原因安静提示(错误标准件同型)并可关闭。 */

import { useEffect, useRef } from "react";
import { AudioLines, MicOff, PhoneOff, X } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { cn } from "@kongming/api-client/utils";
import {
  smoothAudioLevel,
  type CallPhase,
  type FailureReason,
} from "@kongming/api-client/voice-call";

const PHASE_TEXT: Record<Exclude<CallPhase, "failed">, string> = {
  idle: "",
  connecting: "通话连接中…",
  listening: "聆听中,请说出你的问题",
  querying: "正在为您查询…",
  speaking: "正在播报,开口即可打断",
};

const FAILURE_TEXT: Record<FailureReason, string> = {
  mic: "麦克风不可用,请在浏览器允许使用麦克风后重试",
  forbidden: "当前账号没有通话权限",
  not_enabled: "通话功能未启用",
  exists: "已有一通通话在进行,请先挂断后再拨",
  busy: "当前通话人数已满,请稍后再试",
  rate_limited: "操作太频繁,请稍后再试",
  daily_limit: "近期通话次数已达上限,请稍后再试",
  ice: "当前网络不支持语音通话,请检查网络后重试",
  network: "通话不可用,请稍后重试",
};

interface AudioLevels {
  user: number;
  model: number;
}

interface VoiceCallBarProps {
  phase: CallPhase;
  muted: boolean;
  /** 建立失败的原因(仅失败相位用于提示文案) */
  failure: FailureReason | null;
  /** 实时读取用户/模型音频归一化音量 [0, 1] */
  sampleAudioLevels?: () => AudioLevels;
  onHangUp: () => void;
}

/** 失败态安静提示条(错误标准件同型)。 */
function VoiceCallFailureBanner({
  failure,
  onClose,
}: {
  failure: FailureReason | null;
  onClose: () => void;
}) {
  return (
    <div className="px-4 pb-2">
      <div
        role="alert"
        className="mx-auto flex max-w-3xl items-center gap-3 rounded-md border border-destructive/60 bg-destructive/5 px-3 py-2 text-sm text-destructive"
      >
        <span className="flex-1">{FAILURE_TEXT[failure ?? "network"]}</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="关闭"
          className="hover:bg-destructive/10 hover:text-destructive"
          onClick={onClose}
        >
          <X className="size-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}

/** 通话中声音驱动的 Siri 式小球浮层(纯语义 token,rAF 直接驱动 DOM 变换)。 */
export function VoiceCallBar({
  phase,
  muted,
  failure,
  sampleAudioLevels,
  onHangUp,
}: VoiceCallBarProps) {
  const outerRingRef = useRef<HTMLDivElement | null>(null);
  const midRingRef = useRef<HTMLDivElement | null>(null);
  const coreOrbRef = useRef<HTMLDivElement | null>(null);

  const active = phase !== "idle" && phase !== "failed";

  useEffect(() => {
    if (!active) return;
    let rafId = 0;
    let smoothed = 0;

    const tick = () => {
      const levels = sampleAudioLevels?.() ?? { user: 0, model: 0 };
      let target = 0;
      if (!muted) {
        if (phase === "speaking") target = levels.model;
        else if (phase === "listening") target = levels.user;
        else target = Math.max(levels.user, levels.model);
      }
      smoothed = smoothAudioLevel(smoothed, target);
      // 静默时留轻微呼吸底量,说话/播报时随音量张开
      const energy =
        phase === "connecting" || phase === "querying" ? Math.max(0.08, smoothed) : smoothed;

      if (outerRingRef.current) {
        outerRingRef.current.style.transform = `scale(${(1 + energy * 0.55).toFixed(3)})`;
        outerRingRef.current.style.opacity = `${(0.25 + energy * 0.55).toFixed(3)}`;
      }
      if (midRingRef.current) {
        midRingRef.current.style.transform = `scale(${(1 + energy * 0.32).toFixed(3)})`;
        midRingRef.current.style.opacity = `${(0.4 + energy * 0.5).toFixed(3)}`;
      }
      if (coreOrbRef.current) {
        coreOrbRef.current.style.transform = `scale(${(1 + energy * 0.16).toFixed(3)})`;
      }
      rafId = window.requestAnimationFrame(tick);
    };

    rafId = window.requestAnimationFrame(tick);
    return () => {
      window.cancelAnimationFrame(rafId);
    };
  }, [active, muted, phase, sampleAudioLevels]);

  if (phase === "idle") return null;
  if (phase === "failed") {
    return <VoiceCallFailureBanner failure={failure} onClose={onHangUp} />;
  }

  const text = muted ? "已静音" : PHASE_TEXT[phase];

  return (
    <div className="px-4 pb-2">
      <div
        role="status"
        aria-live="polite"
        className="mx-auto flex w-full max-w-md items-center gap-4 rounded-2xl border border-border bg-card/95 px-4 py-3 shadow-lg backdrop-blur-md"
      >
        <div className="relative flex size-16 shrink-0 items-center justify-center" aria-hidden>
          <div
            ref={outerRingRef}
            className="bg-gild/20 absolute inset-1 rounded-full transition-opacity duration-150"
          />
          <div
            ref={midRingRef}
            className="border-gild/40 bg-primary/20 absolute inset-2.5 rounded-full border"
          />
          <div
            ref={coreOrbRef}
            className={cn(
              "from-primary via-grad-from to-gild text-primary-foreground relative flex size-10 items-center justify-center rounded-full bg-linear-to-br shadow-md",
              (phase === "connecting" || phase === "querying") && "animate-pulse",
            )}
          >
            {muted ? <MicOff className="size-4" /> : <AudioLines className="size-4" />}
          </div>
        </div>

        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">{text}</p>
        </div>

        <Button
          variant="destructive"
          size="icon"
          className="shrink-0 rounded-full"
          aria-label="挂断"
          title="挂断语音通话"
          onClick={onHangUp}
        >
          <PhoneOff className="size-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
