/* 会话侧栏(web-nav spec「会话侧栏交互」):
   列表(updated_at 倒序,服务端序)/当前高亮/切换/删除确认;
   新建=导航 /chat,零后端请求(首问自动建会话)。 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, MessageSquare, Trash2 } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router";

import { api } from "@/lib/api";
import { useTenant } from "@/lib/tenant-store";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";

function timeLabel(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

export function SessionSidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { current } = useTenant();
  const { data: sessions, isLoading } = useQuery({
    queryKey: ["sessions", current],
    queryFn: () => api.get<{ id: string; title: string; updated_at: string }[]>("/chat/sessions"),
  });
  const { pathname } = useLocation();
  const activeId = pathname.startsWith("/chat/") ? pathname.slice("/chat/".length) : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="px-2 pb-1 text-xs font-medium text-muted-foreground">会话</p>
      <ScrollArea className="min-h-24 flex-1 rounded-md">
        {isLoading ? (
          <div className="space-y-2 p-1">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : !sessions?.length ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">尚无会话,问第一句即开卷。</p>
        ) : (
          <ul className="space-y-0.5 pr-2">
            {sessions.map((s) => (
              <li key={s.id} className="group relative">
                <Link
                  to={`/chat/${s.id}`}
                  onClick={onNavigate}
                  className={
                    "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors " +
                    (s.id === activeId
                      ? "bg-accent text-accent-foreground"
                      : "text-foreground hover:bg-accent/60")
                  }
                >
                  <MessageSquare
                    className={"size-3.5 shrink-0 " + (s.id === activeId ? "text-primary" : "text-muted-foreground")}
                    aria-hidden
                  />
                  <span className="min-w-0 flex-1 truncate" title={s.title}>
                    {s.title}
                  </span>
                  <span className="shrink-0 text-[10px] text-muted-foreground group-hover:invisible">
                    {timeLabel(s.updated_at)}
                  </span>
                </Link>
                {s.id === activeId && <DeleteSessionButton id={s.id} />}
              </li>
            ))}
          </ul>
        )}
      </ScrollArea>
    </div>
  );
}

function DeleteSessionButton({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { current } = useTenant();

  async function remove(): Promise<void> {
    setBusy(true);
    setFailed(false);
    try {
      await api.delete(`/chat/sessions/${id}`);
    } catch (err) {
      if ((err as Error & { status?: number }).status !== 404) {
        // 404=他端已删,视为已删照常收敛;其余失败保持对话框开启,提示可见
        setFailed(true);
        setBusy(false);
        return;
      }
    }
    setBusy(false);
    setOpen(false);
    // 已删会话的 messages 键不再 invalidate(重取只会 404);路由离开自然卸载
    await qc.invalidateQueries({ queryKey: ["sessions", current] });
    // 当前会话被删:视图回新建态(spec「删除会话需确认且即时收敛」)
    navigate("/chat", { replace: true });
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) setFailed(false); // 重开清残留错误
      }}
    >
      <AlertDialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="删除会话"
          className="absolute top-1/2 right-1 hidden -translate-y-1/2 group-hover:flex group-focus-within:flex max-md:flex"
        >
          <Trash2 className="text-muted-foreground" aria-hidden />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除此会话?</AlertDialogTitle>
          <AlertDialogDescription>
            会话及其全部消息将被移除,此步不可挽回。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {failed && <p className="text-sm text-destructive">删除未成,请稍后再试。</p>}
        <AlertDialogFooter>
          <AlertDialogCancel>再想想</AlertDialogCancel>
          {/* 受控对话框:关闭时机由 remove 决定,Action 需阻默认关 */}
          <AlertDialogAction
            disabled={busy}
            onClick={(e) => {
              e.preventDefault();
              void remove();
            }}
          >
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
