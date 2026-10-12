/* 对话页(web-nav spec「对话区渲染」+ 017 流式 + 039 冷启动建议问题):
   历史回显 + 流式提问(阶段点亮/SQL 提前/summary 打字机;终态由 AssistantCard
   接管)+ 新会话品牌空态(下方建议问句 chips,点击即问)。路由参数 sessionId
   是会话选择的单源(刷新/直达天然成立)。
   错误路径:流异常回填草稿并提示;会话被他端删除(404)时收敛回新建态。
   取消语义(078):切会话/新建/切租户/数据源切换/卸载时中止在途流;流式期间
   提供显式「停止接收」入口——前端中止读取,控制面仍跑完落库(既有契约:
   主动取消不等同于后端中止),故文案只称"不再接收/后台完成",禁"已取消"。
   滚动跟随(078):近底才自动贴底(阈值近底判定纯函数),用户上滚查看历史/
   长结果表时不被拽回;出现「回到底部」浮标,点击即回底并恢复跟随。
   中流刷新/停止后:末条为 USER 即触发有界恢复回查(控制面保证写终态行),
   应答自行出现;预算耗尽即停,不无限轮询。 */

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileText,
  ArrowDown,
  Feather,
  Info,
  Loader2,
  Mic,
  Paperclip,
  SendHorizontal,
  StopCircle,
  TriangleAlert,
  X,
} from "lucide-react";
import { useNavigate, useParams } from "react-router";

import { AssistantCard } from "@/components/assistant-card";
import { SessionDrawer } from "@/components/session-drawer";
import { ChatBubble } from "@/components/chat-bubble";
import { StreamingTurn } from "@/components/streaming-turn";
import { ToolConfirmCard } from "@/components/tool-confirm-card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { api, humanizeErrorText, type ChatQueryOut, type SessionMessageOut } from "@/lib/api";
import { useAgentMode } from "@/lib/agent-mode-store";
import { useAuth } from "@/lib/auth-store";
import { hasPerm } from "@/lib/nav";
import { getSuggestions, transcribeAudio, uploadAttachment } from "@/lib/chat-api";
import { generateReport } from "@/lib/report-api";
import { useDatasource } from "@/lib/datasource-store";
import {
  RECOVERY_POLL_BUDGET_MS,
  RECOVERY_POLL_INTERVAL_MS,
  recoveryExpired,
  needsTurnRecovery,
} from "@/lib/recovery-poll";
import { isNearBottom } from "@/lib/scroll-follow";
import { useTenant } from "@/lib/tenant-store";

function EmptyState({
  items,
  loading,
  onAsk,
}: {
  items: string[];
  loading: boolean;
  onAsk: (q: string) => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-5 px-6 text-center">
      <div className="space-y-3">
        <span className="flex size-14 items-center justify-center rounded-2xl bg-primary mx-auto">
          <Feather className="size-7 text-primary-foreground" aria-hidden />
        </span>
        <h2 className="text-xl font-semibold tracking-widest text-foreground">万事,问孔明。</h2>
        <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
          数据之事,直问即可。问库存、问销量、问趋势—— 运筹帷幄之事,交给军师。
        </p>
      </div>
      {/* 039 冷启动建议:加载骨架/空清单或失败静默隐藏(不阻塞品牌空态) */}
      {loading && items.length === 0 && (
        <div className="flex max-w-lg flex-wrap justify-center gap-2" aria-hidden>
          <Skeleton className="h-8 w-44 rounded-full" />
          <Skeleton className="h-8 w-36 rounded-full" />
          <Skeleton className="h-8 w-40 rounded-full" />
        </div>
      )}
      {items.length > 0 && (
        <div className="flex max-w-lg flex-wrap justify-center gap-2">
          {items.map((q) => (
            <Button
              key={q}
              variant="outline"
              size="sm"
              className="rounded-full font-normal text-muted-foreground"
              onClick={() => onAsk(q)}
            >
              {q}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 终态快照(R1-B1):result 到达即由 ChatQueryOut 乐观构造本轮消息渲染,
 * 消息历史 refetch 含该轮(message_id 命中)后自然替换——消灭「先清空后补位」
 * 的空白间隙;persist=false 无历史可对照时保留至下一轮/切换上下文。 */
/** 030 附件徽标(user 气泡两处共用:历史回显与即时快照) */
function AttachmentBadge({ items }: { items: { [key: string]: unknown }[] }) {
  if (items.length === 0) return null;
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground">
      <Paperclip className="size-3" aria-hidden />
      {items.length} 张图片:
      {items.map((a) => String(a.filename ?? "")).join(", ")}
    </span>
  );
}

function toSyntheticMessage(out: ChatQueryOut): SessionMessageOut {
  // 044 四态:NO_DATA=知识未覆盖如实说明(中性,非失败)
  const status = out.success
    ? "SUCCESS"
    : out.verdict === "BLOCKED"
      ? "REJECTED"
      : out.verdict === "NO_DATA"
        ? "NO_DATA"
        : "FAILED";
  return {
    // message_id 缺失(落库容错分支)置空串:FeedbackActions 的 !msg.id 守卫
    // 与 EXPLAIN 入口({msg.sql && ...}同上)天然不渲染,避免非 UUID 直传 422(R3-B2)
    id: out.message_id ?? "",
    role: "ASSISTANT",
    question: null,
    summary: out.summary || null,
    sql: out.sql || null,
    status,
    error: out.error,
    error_detail: out.error_detail ?? null, // 047:技术详情折叠(快照与回显同源)
    result: {
      columns: out.columns,
      rows: out.rows,
      insights: out.insights,
      stats: out.stats,
      chart: out.chart,
      // 049:候选快照同源(与 041 column_labels 同款先例)——漏带会使快照期
      // 回落单图绑定、历史 refetch 后切换到候选绑定,产生一次视觉跳变
      chart_candidates: out.chart_candidates ?? [],
      error: out.error,
      error_detail: out.error_detail ?? null, // 047 同源
      // 026:终态快照携带产物卡(与历史回显 MessageResultOut.artifacts 同形),
      // 缺档空数组 → assistant-card 回落既有形态,与读档重算兜底口径一致
      artifacts: out.artifacts ?? [],
      // 041:呈现层列标签快照同源(与 artifacts/attachments 同款先例)——
      // 漏带会使快照期表头物理名、历史 refetch 后闪变业务名(评审实锤)
      column_labels: out.column_labels ?? [],
    },
    rating: null,
    corrected_sql: null,
    feedback_comment: null,
    attachments: out.attachments ?? [], // 030:终态快照同源
  };
}

/* 078 任务 2:历史消息项按数据字段 memo——比较器只比 msg 引用(react-query
   结构共享保证未变消息引用稳定),回调身份/内联对象不参与比较;过程事件不再
   广播到页面级 state,本组件不再随每个流事件重渲染。 */
const MessageRow = memo(function MessageRow({ m }: { m: SessionMessageOut }) {
  if (m.role === "USER") {
    return (
      <div className="flex flex-col items-end gap-1">
        <ChatBubble role="user">{m.question}</ChatBubble>
        <AttachmentBadge items={m.attachments ?? []} />
      </div>
    );
  }
  return (
    <div className="max-w-full">
      <AssistantCard msg={m} />
    </div>
  );
});

function isStatus(err: unknown, status: number): boolean {
  return (err as Error & { status?: number }).status === status;
}

/** 在途流暂停态(078):发起时一次性创建,过程事件不再经它广播 */
interface StreamingTurnStart {
  question: string;
  body: unknown;
  attSnapshot: { id: string; filename: string }[];
  ac: AbortController;
}

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  // 088:会话内一键生成报告(入口 a:既有问答产物为素材;新会话无 sid=直接指令)
  const canGenerateReport = hasPerm(
    useAuth((s) => s.user),
    "report:generate",
  );
  const [reportOpen, setReportOpen] = useState(false);
  const [reportInstruction, setReportInstruction] = useState("");
  const [reportTitle, setReportTitle] = useState("");
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportBusy, setReportBusy] = useState(false);

  const { current } = useTenant();
  const datasource = useDatasource((s) => s.current);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState<StreamingTurnStart | null>(null);
  const [finalized, setFinalized] = useState<{ question: string; out: ChatQueryOut } | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  // 078 任务 3:主动停止接收后的口径提示(禁"已取消"类误导措辞)
  const [stoppedNotice, setStoppedNotice] = useState(false);
  const agentMode = useAgentMode((s) => s.mode);
  const setAgentMode = useAgentMode((s) => s.setMode);
  // 019 待确认写工具(独立于 finalized 快照:快照退位由历史接管后确认卡
  // 仍在,直到用户确认/拒绝或切换上下文——响应级瞬态,不落库不回显)
  const [pendingConfirm, setPendingConfirm] = useState<{
    pending: ChatQueryOut["pending_tool_calls"];
    sessionId: string;
  } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // 流发起时的上下文(守卫基准):迟到事件与当前不一致即丢弃(防串上下文)
  const streamCtxRef = useRef<{
    sid: string | null;
    tenant: string | null;
    ds: string | null;
  } | null>(null);
  // 本流自建会话的导航标记(R2-B2:取消闸豁免依据)
  const selfNavigatedRef = useRef<string | null>(null);
  // navigate/invalidate 回调里读最新值的稳定引用(sid 归一 null:useParams
  // 无参数时为 undefined,与流发起上下文的 null 归一比对——不归一则守卫恒 false)
  async function submitGenerateReport(): Promise<void> {
    const instruction = reportInstruction.trim();
    if (reportBusy || !instruction) return;
    setReportBusy(true);
    setReportError(null);
    try {
      const out = await generateReport({
        instruction,
        datasource_key: datasource,
        session_id: sessionIdRef.current,
        title: reportTitle.trim() || null,
      });
      setReportOpen(false);
      setReportInstruction("");
      setReportTitle("");
      navigate(`/reports/${out.report_id}`);
    } catch (e) {
      setReportError(e instanceof Error ? e.message : "发起生成未成,请稍后再试。");
    } finally {
      setReportBusy(false);
    }
  }

  const sessionIdRef = useRef<string | null>(sessionId ?? null);
  sessionIdRef.current = sessionId ?? null;
  const tenantRef = useRef(current);
  tenantRef.current = current;
  const dsRef = useRef(datasource);
  dsRef.current = datasource;
  // 078 任务 1:滚动容器视口引用 + 近底跟随状态。视口经 ScrollArea Root 探
  // data-slot 取得(不改双仓标准件 ui/scroll-area.tsx;Radix 隐藏原生滚动条,
  // 判定不能挂 window)
  const scrollRootRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [followBottom, setFollowBottom] = useState(true);
  // 强制贴底标记:切会话/用户主动提问时消费(内容 effect 先于跟随判定)
  const forceBottomRef = useRef(false);

  const messages = useQuery({
    // 键含租户+数据源双维度:切换即换档,不跨上下文残留
    queryKey: ["messages", sessionId ?? "-", current, datasource],
    queryFn: () => api.get<SessionMessageOut[]>(`/chat/sessions/${sessionId}/messages`),
    enabled: sessionId !== undefined,
  });

  // 039 冷启动建议:仅新建态干净分支启用;键含租户+数据源双维度,切换即换档
  const suggestions = useQuery({
    queryKey: ["chat-suggestions", current, datasource],
    queryFn: () => getSuggestions(datasource),
    enabled: !sessionId && !streaming && !finalized && !askError,
  });

  // 会话已被删除(他端/本端竞态):提示并收敛回新建态,不留空白死态
  useEffect(() => {
    if (messages.isError && isStatus(messages.error, 404)) {
      void qc.invalidateQueries({ queryKey: ["sessions", current] });
      navigate("/chat", { replace: true });
    }
  }, [messages.isError, messages.error, qc, navigate, current]);

  // 取消闸(017):上下文变化(切会话/新建/切租户/切数据源)即中止在途流并
  // 清残留呈现;卸载时同样中止(闭包捕获最新 refs)。
  // R2-B2 豁免:本流自建会话的 navigate 也会触发 sessionId 变化——该导航
  // 是终态接管的一部分,不得清快照(否则接管间隙以骨架屏闪变复活)。
  useEffect(() => {
    if (selfNavigatedRef.current !== null && selfNavigatedRef.current === sessionId) {
      selfNavigatedRef.current = null;
      return; // 本流自己的导航:流已终止,呈现交给快照/历史
    }
    selfNavigatedRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    streamCtxRef.current = null;
    setStreaming(null);
    setFinalized(null);
    setAskError(null);
    setStoppedNotice(false);
    setPendingConfirm(null); // 切换上下文丢弃待确认入口(瞬态语义)
  }, [sessionId, current, datasource]);
  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  // 终态快照退位:消息历史 refetch 已含该轮(message_id 命中)即撤快照
  useEffect(() => {
    if (!finalized) return;
    const mid = finalized.out.message_id;
    if (mid && messages.data?.some((m) => m.id === mid)) {
      setFinalized(null);
    }
  }, [messages.data, finalized]);

  // 078 任务 1:视口引用挂载后取一次(ScrollArea 常驻,分支切换不卸载)
  useEffect(() => {
    const root = scrollRootRef.current;
    if (!root) return;
    viewportRef.current = root.querySelector<HTMLDivElement>('[data-slot="scroll-area-viewport"]');
  }, []);

  // 078 任务 1:滚动监听只翻转布尔(状态更新限于变化帧,防每帧 setState)
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onScroll = () => {
      const near = isNearBottom(el.scrollTop, el.scrollHeight, el.clientHeight);
      setFollowBottom((prev) => (prev === near ? prev : near));
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // 078 任务 1:切换会话强制贴底(用户主动进入,从最新开始读)
  useEffect(() => {
    forceBottomRef.current = true;
    setFollowBottom(true);
  }, [sessionId]);

  // 078 任务 1:新旧消息/在途占位变化后——近底才跟随;非跟随即让用户留在
  // 原处(浮标提示可回底)。双 rAF:等 React 提交 + 布局完成再度量。
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const force = forceBottomRef.current;
    forceBottomRef.current = false;
    if (!followBottom && !force) return;
    let inner = 0;
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => {
        el.scrollTo({ top: el.scrollHeight, behavior: force ? "auto" : "smooth" });
      });
    });
    return () => {
      cancelAnimationFrame(outer);
      cancelAnimationFrame(inner);
    };
  }, [messages.data, streaming, followBottom, sessionId]);

  // 078 任务 3:有界恢复回查——末条 USER 且无 ASSISTANT(中流刷新/停止接收)
  // 即服务端有轮次在收尾;budget 跨数据变更累计(防每次 refetch 重置变无限轮询),
  // 终态出现或超时即停;离开路由由 cleanup 兜底(无残留轮询)。
  const recoveryDeadlineRef = useRef(0);
  useEffect(() => {
    if (!sessionId) return;
    if (!needsTurnRecovery(messages.data)) {
      recoveryDeadlineRef.current = 0;
      return;
    }
    if (recoveryDeadlineRef.current === 0) {
      recoveryDeadlineRef.current = Date.now() + RECOVERY_POLL_BUDGET_MS;
    }
    const timer = window.setInterval(() => {
      void messages.refetch();
      if (recoveryExpired(recoveryDeadlineRef.current, Date.now())) {
        window.clearInterval(timer);
        recoveryDeadlineRef.current = 0;
      }
    }, RECOVERY_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
    // refetch 身份在 react-query v5 查询实例级稳定,无需整体 messages 依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, messages.data, messages.refetch]);

  /** 守卫:流回调仅在「流的发起上下文 == 当前上下文」时生效(迟到即丢弃) */
  function inOriginCtx(): boolean {
    const ctx = streamCtxRef.current;
    return (
      ctx !== null &&
      ctx.sid === sessionIdRef.current &&
      ctx.tenant === tenantRef.current &&
      ctx.ds === dsRef.current
    );
  }

  function finalizeOk(out: ChatQueryOut, question: string): void {
    if (!inOriginCtx()) return;
    setStreaming(null);
    setStoppedNotice(false);
    setAskError(null);
    // 019:待确认调用独立留存(快照退位不带走确认入口);
    // ?? [] 兜底:前后端版本错位(滚动部署)缺键时不炸(R1-D3)
    const pendingCalls = out.pending_tool_calls ?? [];
    if (pendingCalls.length > 0 && out.session_id) {
      setPendingConfirm({ pending: pendingCalls, sessionId: out.session_id });
    }
    setFinalized({ question, out }); // 乐观渲染本轮,历史 refetch 后替换(R1-B1)
    // 先导航(消空态闪回),再失效侧栏;新会话 URL 即状态(与原同步路径同构);
    // 自建导航打标——取消闸据此豁免(R2-B2)
    if (out.session_id && out.session_id !== sessionIdRef.current) {
      selfNavigatedRef.current = out.session_id;
      navigate(`/chat/${out.session_id}`, { replace: true });
    }
    void qc.invalidateQueries({ queryKey: ["sessions", tenantRef.current] });
    if (out.session_id === sessionIdRef.current) {
      void qc.invalidateQueries({
        queryKey: ["messages", out.session_id, tenantRef.current, dsRef.current],
      });
    }
  }

  function finalizeError(
    message: string,
    question: string,
    attSnapshot: { id: string; filename: string }[] = [],
  ): void {
    if (!inOriginCtx()) return;
    setStreaming(null);
    setAskError(message);
    // 失败回填,但尊重用户等待期间的新输入
    setDraft((prev) => (prev.trim() ? prev : question));
    // R1-F4:传输层失败时附件未被消费(agent 未取件),chips 恢复可重试;
    // 尊重等待期间用户新贴的附件(去重合并)
    if (attSnapshot.length) {
      setAttachments((prev) => {
        const seen = new Set(prev.map((a) => a.id));
        return [...prev, ...attSnapshot.filter((a) => !seen.has(a.id))];
      });
    }
  }

  // 030:附件状态(已上传附件 chips;发送后清空)
  const [attachments, setAttachments] = useState<{ id: string; filename: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // 114:语音输入(MediaRecorder → /chat/stt → 文本填入输入框,用户可修正后
  // 发送;Escape 取消丢弃)。音频即转即弃,前端不留存。
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  function cancelRecording(): void {
    recorderRef.current?.stream.getTracks().forEach((t) => t.stop());
    recorderRef.current = null;
    chunksRef.current = [];
    setRecording(false);
  }

  async function onMicClick(): Promise<void> {
    if (recording) {
      recorderRef.current?.stop(); // onstop 负责收尾+转写
      return;
    }
    setAskError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        recorderRef.current = null;
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        if (blob.size < 1024) return; // 近乎无声的误触
        setTranscribing(true);
        transcribeAudio(blob)
          .then((text) => {
            if (!text) {
              setAskError("没有听清,请再试一次。");
              return;
            }
            setDraft((prev) => (prev ? `${prev} ${text}` : text));
          })
          .catch((err) =>
            setAskError(err instanceof Error ? err.message : "语音识别未成,请稍后再试。"),
          )
          .finally(() => setTranscribing(false));
      };
      rec.start();
      recorderRef.current = rec;
      setRecording(true);
    } catch {
      setAskError("无法访问麦克风,请检查浏览器权限。");
    }
  }

  useEffect(() => {
    if (!recording) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancelRecording();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [recording]);

  async function onPickFile(e: React.ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同文件可重选(009 R2-S2 教训)
    if (!file) return;
    if (attachments.length >= 3) {
      setAskError("一次最多引用 3 张图片。");
      return;
    }
    setUploading(true);
    setAskError(null);
    try {
      const out = await uploadAttachment(file);
      setAttachments((prev) => [...prev, { id: out.attachment_id, filename: out.filename }]);
    } catch (err) {
      setAskError(err instanceof Error ? err.message : "附件上传未成,请稍后再试。");
    } finally {
      setUploading(false);
    }
  }

  async function submit(text?: string): Promise<void> {
    // 039:建议 chips 直发(text 显式传入,保留草稿不被清空);缺省=输入框草稿
    const fromDraft = text === undefined;
    const q = (text ?? draft).trim();
    // R1-S2:与按钮/契约对齐(question min_length=1)——曾放行纯附件,
    // Enter 可绕过按钮禁用直撞 422 且 chips 已清空
    // 审查循环 B1:上传中禁发——此时提交 attIds 不含上传中附件,
    // 问答无图且完成后 chips 孤立出现(与按钮禁用态对齐)
    if (!q || uploading || streaming !== null) return; // 防重复提交
    const attIds = attachments.map((a) => a.id);
    const attSnapshot = attachments; // R1-F4:失败恢复用快照
    setAttachments([]);
    if (fromDraft) setDraft("");
    setAskError(null);
    setStoppedNotice(false);
    setFinalized(null); // 新一轮提问顶掉上一轮快照
    const ac = new AbortController();
    abortRef.current = ac;
    streamCtxRef.current = { sid: sessionId ?? null, tenant: current, ds: datasource };
    // 078:在途态一次性创建(占位只插一次);过程事件由 StreamingTurn 自持,
    // 不再经页面级 state 逐事件广播
    setStreaming({
      question: q,
      ac,
      attSnapshot,
      body: {
        question: q,
        datasource_key: datasource, // 选中的数据源随查询发送(008)
        agent_mode: agentMode, // 019:ReAct 内核开关(缺省 recipe 零回归)
        ...(attIds.length ? { attachment_ids: attIds } : {}), // 030
        ...(sessionId ? { session_id: sessionId } : {}),
      },
    });
    // 主动提问:强制贴底(用户明确要从最新开始看本轮)
    forceBottomRef.current = true;
    setFollowBottom(true);
  }

  /** 078 任务 3:停止接收——中止前端读取;控制面仍跑完落库(既有契约),
   *  文案只述"不再接收/后台完成",问句回框可重发 */
  function stopStreaming(): void {
    const s = streaming;
    abortRef.current?.abort();
    abortRef.current = null;
    streamCtxRef.current = null;
    setStreaming(null);
    if (s) {
      setDraft((prev) => (prev.trim() ? prev : s.question));
      setStoppedNotice(true);
    }
  }

  // 078 任务 2.3:快照消息 useMemo 稳定引用(finalized 对象仅在轮次边界变)
  const finalizedMsg = useMemo(
    () => (finalized ? toSyntheticMessage(finalized.out) : null),
    [finalized],
  );

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {/* 窄屏页头(mobile-experience B2.3):会话抽屉唯一入口;宽屏侧栏常驻不渲染 */}
      <header className="flex items-center gap-2 border-b border-border px-3 py-2 md:hidden">
        <SessionDrawer />
        <span className="text-sm font-medium tracking-widest text-foreground">孔明</span>
      </header>
      <ScrollArea ref={scrollRootRef} className="min-h-0 flex-1">
        <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
          {messages.isLoading && sessionId && !finalized ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-3/4" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : !sessionId && !streaming && !finalized && !askError ? (
            <div className="h-[60vh]">
              <EmptyState
                items={suggestions.data?.items ?? []}
                loading={suggestions.isLoading}
                onAsk={(q) => void submit(q)}
              />
            </div>
          ) : (
            <>
              {messages.data?.map((m) => (
                <MessageRow key={m.id} m={m} />
              ))}
              {finalized && !streaming && finalizedMsg && (
                <>
                  <div className="flex flex-col items-end gap-1">
                    <ChatBubble role="user">{finalized.question}</ChatBubble>
                    <AttachmentBadge items={finalized.out.attachments ?? []} />
                  </div>
                  <div className="max-w-full">
                    <AssistantCard msg={finalizedMsg} />
                  </div>
                </>
              )}
              {streaming && (
                <>
                  <div className="flex justify-end">
                    <ChatBubble role="user">{streaming.question}</ChatBubble>
                  </div>
                  <div className="max-w-full">
                    <StreamingTurn
                      body={streaming.body}
                      signal={streaming.ac.signal}
                      onResult={(out) => finalizeOk(out, streaming.question)}
                      onError={(message) =>
                        finalizeError(message, streaming.question, streaming.attSnapshot)
                      }
                    />
                  </div>
                </>
              )}
              {pendingConfirm && (
                <ToolConfirmCard
                  pending={pendingConfirm.pending}
                  sessionId={pendingConfirm.sessionId}
                  onAllResolved={() => setPendingConfirm(null)}
                />
              )}
              {/* 078 任务 3:停止接收后的口径呈现——禁"已取消/已停止生成"类误导措辞 */}
              {stoppedNotice && (
                <div
                  className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm"
                  role="status"
                >
                  <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <div>
                    <p className="text-foreground">已不再接收本轮输出。</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      该轮问答仍在后台完成,可稍后回看;问句已回到输入框,也可立即重发。
                    </p>
                  </div>
                </div>
              )}
              {askError && (
                <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
                  <div>
                    <p className="text-foreground">此问未能送达:{humanizeErrorText(askError)}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      问句已回到输入框,可重试。
                    </p>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </ScrollArea>

      {/* 078 任务 1.4:回到底部浮标——非跟随时出现;到达底部(滚动监听翻转
          followBottom)或点击回底后消失 */}
      {!followBottom && (
        <div className="pointer-events-none absolute inset-x-0 bottom-28 z-10 flex justify-center">
          <Button
            variant="secondary"
            size="sm"
            className="pointer-events-auto rounded-full shadow-md"
            aria-label="回到底部"
            onClick={() => {
              forceBottomRef.current = true;
              setFollowBottom(true);
              const el = viewportRef.current;
              el?.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
            }}
          >
            <ArrowDown aria-hidden />
            回到底部
          </Button>
        </div>
      )}

      {/* 输入区(mobile-experience B4.1):mb-3 与 BottomNav 呼吸分隔;
          底部安全区由导航层统一避让(bottom-nav pb-safe),此处不再叠加 */}
      <div className="mb-3 border-t border-border bg-background px-4 py-3 md:mb-0">
        <div className="mx-auto max-w-3xl">
          {/* 019:Agent 内核开关(自主工具调用=ReAct;缺省 recipe 零回归);
              UI 命名「运筹模式」与空态文案「运筹帷幄之事,交给军师」同源,实现术语不入用户文案 */}
          <div className="mb-2 flex items-center justify-between gap-2">
            {/* 附件钮置于工具行左位:输入行只留 [输入框|发送],水平间距以容器
                px-4 为单源;热区盒 24px(图标几何居中仅 4px),触控面经伪元素补至 40px */}
            <Button
              variant="ghost"
              size="icon-xs"
              className="after:-inset-2"
              onClick={() => fileRef.current?.click()}
              disabled={uploading || streaming !== null}
              aria-label="添加图片附件"
              title="添加图片(png/jpg/webp/gif,≤10MB)"
            >
              {/* 图标显式钉 size-4(icon-xs 默认 12px 经 :not 排除),与发送键同大 */}
              {uploading ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Paperclip className="size-4" aria-hidden />
              )}
            </Button>
            {/* 114 语音输入:录音中 Mic 变警示色+脉冲;转写中文本填入输入框,
                用户可修正后发送;Escape 取消丢弃 */}
            <Button
              variant="ghost"
              size="icon-xs"
              className="after:-inset-2"
              onClick={() => void onMicClick()}
              disabled={transcribing || uploading || streaming !== null}
              aria-label={recording ? "停止录音" : "语音输入"}
              title={recording ? "停止录音并转写(Escape 取消)" : "语音输入(说完点停止)"}
            >
              {transcribing ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Mic
                  className={recording ? "size-4 animate-pulse text-destructive" : "size-4"}
                  aria-hidden
                />
              )}
            </Button>
            <div className="flex items-center gap-2">
              {canGenerateReport && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  className="after:-inset-2"
                  onClick={() => setReportOpen(true)}
                  disabled={streaming !== null}
                  aria-label="生成报告"
                  title="基于本会话问答生成分析报告"
                >
                  <FileText className="size-4" aria-hidden />
                </Button>
              )}
              <Switch
                id="agent-mode"
                checked={agentMode === "react"}
                onCheckedChange={(on) => setAgentMode(on ? "react" : "recipe")}
                aria-label="运筹模式"
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Label
                    htmlFor="agent-mode"
                    className="cursor-pointer text-xs text-muted-foreground"
                  >
                    运筹模式
                  </Label>
                </TooltipTrigger>
                <TooltipContent className="max-w-72">
                  AI 将自主规划步骤、调用工具求解,适合复杂问题;日常提问无需开启。
                </TooltipContent>
              </Tooltip>
            </div>
          </div>
          {/* 088:一键生成报告对话框(入口 a/b 同源;发起后跳报告详情看进度) */}
          <Dialog open={reportOpen} onOpenChange={setReportOpen}>
            <DialogContent className="sm:max-w-lg">
              <DialogHeader>
                <DialogTitle>生成分析报告</DialogTitle>
                <DialogDescription>
                  {sessionId
                    ? "以本会话既有问答为素材生成;也可只写指令,由 AI 自主取数。"
                    : "描述报告目标与数据范围,AI 经问数链路自主取数。"}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-3">
                <div className="space-y-1">
                  <Label htmlFor="report-instruction">报告要求</Label>
                  <Textarea
                    id="report-instruction"
                    value={reportInstruction}
                    onChange={(e) => setReportInstruction(e.target.value)}
                    rows={3}
                    maxLength={2000}
                    placeholder={
                      sessionId ? "例:面向周会,重点讲异常与趋势" : "例:出一份近 30 天销售分析报告"
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="report-title">标题(可选)</Label>
                  <Input
                    id="report-title"
                    value={reportTitle}
                    onChange={(e) => setReportTitle(e.target.value)}
                    maxLength={200}
                    placeholder="留空则由 AI 拟定"
                  />
                </div>
                {reportError && (
                  <p role="alert" className="text-sm text-destructive">
                    {reportError}
                  </p>
                )}
              </div>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setReportOpen(false)}>
                  取消
                </Button>
                <Button
                  onClick={() => void submitGenerateReport()}
                  disabled={reportBusy || !reportInstruction.trim()}
                >
                  {reportBusy ? "发起中…" : "发起生成"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          {attachments.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pb-1">
              {attachments.map((a) => (
                <Badge key={a.id} variant="secondary">
                  <Paperclip className="size-3" aria-hidden />
                  {a.filename}
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`移除附件 ${a.filename}`}
                    onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                  >
                    <X aria-hidden />
                  </Button>
                </Badge>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp,.gif"
              className="hidden"
              aria-hidden
              tabIndex={-1}
              onChange={(e) => void onPickFile(e)}
            />
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit();
                }
              }}
              placeholder="问计于数据,如问计于孔明…"
              rows={1}
              className="max-h-40 min-h-10 resize-none"
              aria-label="提问"
            />
            {/* 078 任务 3.1:流式期间发送位切换为显式「停止接收」——
                中止读取并立即释放输入;服务端不中止(契约口径),文案对齐 */}
            {streaming ? (
              <Button
                size="icon"
                variant="outline"
                onClick={stopStreaming}
                aria-label="停止接收本轮输出"
                title="停止接收本轮输出(该轮仍在后台完成)"
              >
                <StopCircle aria-hidden />
              </Button>
            ) : (
              <Button
                size="icon"
                onClick={() => void submit()}
                disabled={!draft.trim() || uploading}
                aria-label="发送"
              >
                <SendHorizontal aria-hidden />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
