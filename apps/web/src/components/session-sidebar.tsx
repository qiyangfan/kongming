/* 会话侧栏(web-nav spec「会话侧栏交互」+ 010 chat spec 时间分组/搜索/重命名):
   搜索框(300ms 防抖,服务端 ILIKE)/时间分组(今天/昨天/近 7 天/近 30 天/更早)/
   行内操作菜单(重命名对话框 + 删除受控确认)/当前高亮/切换;
   新建=导航 /chat,零后端请求(首问自动建会话)。 */

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, MessageSquare, MoreVertical, Search } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router";

import { api, type SessionItemOut } from "@/lib/api";
import { listSessions, renameSession } from "@/lib/chat-api";
import { groupSessionsByTime } from "@/lib/session-groups";
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
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
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
  const [input, setInput] = useState("");
  const [q, setQ] = useState("");
  // 300ms 防抖:输入停顿后再发搜索(键含 q 维度,换词即换档)
  useEffect(() => {
    const t = setTimeout(() => setQ(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);
  const { data: sessions, isLoading } = useQuery({
    queryKey: ["sessions", current, q],
    queryFn: () => listSessions(q || undefined),
    // 换词瞬态保留上一档内容,不闪骨架屏(R1-S3)
    placeholderData: (prev) => prev,
  });
  const { pathname } = useLocation();
  const activeId = pathname.startsWith("/chat/") ? pathname.slice("/chat/".length) : null;

  const groups = sessions ? groupSessionsByTime(sessions) : [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative px-2 pb-2">
        <Search
          className="absolute top-1/2 left-4.5 size-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="搜索会话…"
          aria-label="搜索会话"
          className="h-8 pl-7 text-xs"
        />
      </div>
      <ScrollArea className="min-h-24 flex-1 rounded-md">
        {isLoading ? (
          <div className="space-y-2 p-1">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : !sessions?.length ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">
            {q ? "没有匹配的会话。" : "尚无会话,问第一句即开卷。"}
          </p>
        ) : (
          groups.map((g) => (
            <div key={g.label} className="mb-1">
              <p className="px-2 pt-1 pb-0.5 text-3xs font-medium text-muted-foreground">
                {g.label}
              </p>
              <ul className="space-y-0.5 pr-2">
                {g.items.map((s) => (
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
                        className={
                          "size-3.5 shrink-0 " +
                          (s.id === activeId ? "text-primary" : "text-muted-foreground")
                        }
                        aria-hidden
                      />
                      <span className="min-w-0 flex-1 truncate" title={s.title}>
                        {s.title}
                      </span>
                      {/* 时间止步于行尾保留区之外:按钮独占 right-1+size-6=28px
                          固定槽,时间 mr-7(28px)在 Link 内容盒内提前收束、右缘
                          恒落槽外(Link 自身 pr-2 再让出 8px 呼吸)——按钮显现
                          只盖空白,与时间无共槽可能;旧「同槽互换」范式下按钮
                          transition-all 渐隐而时间瞬时回归,移开鼠标必有一瞬
                          重叠,不可缝补 */}
                      <span className="mr-7 shrink-0 text-3xs text-muted-foreground">
                        {timeLabel(s.updated_at)}
                      </span>
                    </Link>
                    <SessionActions session={s} isActive={s.id === activeId} />
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </ScrollArea>
    </div>
  );
}

/* 行内操作(010):重命名对话框 + 删除受控确认;菜单每项会话可达
   (hover/focus-within 显现,移动端常显——006 可达性教训) */
function SessionActions({ session, isActive }: { session: SessionItemOut; isActive: boolean }) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          {/* 显隐用 visibility 而非 display:菜单打开期间鼠标点空白关闭时,
              data-state 与 hover 同时失效,若 display:none 会把 anchor 几何
              清零,Radix Popper 将播放退出动画的弹框重定位到视口左上角闪现;
              visibility:hidden 保留盒几何,退出动画原位播完 */}
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="会话操作"
            className="absolute top-1/2 right-1 invisible -translate-y-1/2 group-hover:visible group-focus-within:visible max-md:visible data-[state=open]:visible"
          >
            <MoreVertical className="text-muted-foreground" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => setRenameOpen(true)}>重命名</DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onSelect={() => setDeleteOpen(true)}>
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {/* 对话框挂菜单外(Radix 焦点陷阱冲突规避),受控开合 */}
      <RenameDialog session={session} open={renameOpen} onOpenChange={setRenameOpen} />
      <DeleteDialog
        session={session}
        isActive={isActive}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
      />
    </>
  );
}

function RenameDialog({
  session,
  open,
  onOpenChange,
}: {
  session: SessionItemOut;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [title, setTitle] = useState(session.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const { current } = useTenant();

  // 每次打开回填当前标题(上次成功/失败的残留不带入)
  useEffect(() => {
    if (open) {
      setTitle(session.title);
      setError(null);
    }
  }, [open, session.title]);

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    const cleaned = title.trim();
    if (!cleaned || busy) return;
    setBusy(true);
    setError(null);
    try {
      await renameSession(session.id, cleaned);
    } catch (err) {
      // 失败保持对话框开启,错误行内可见(007 教训:不得随卸载不可见)
      setError(err instanceof Error ? err.message : "重命名未成,请稍后再试。");
      setBusy(false);
      return;
    }
    setBusy(false);
    onOpenChange(false);
    // 前缀失效:覆盖各搜索词下的列表档
    await qc.invalidateQueries({ queryKey: ["sessions", current] });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重命名会话</DialogTitle>
          <DialogDescription>为「{session.title}」起个新名字。</DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => void submit(e)} className="space-y-3">
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
            autoFocus
            aria-label="会话标题"
          />
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={busy || !title.trim()}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({
  session,
  isActive,
  open,
  onOpenChange,
}: {
  session: SessionItemOut;
  isActive: boolean;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { current } = useTenant();

  async function remove(): Promise<void> {
    setBusy(true);
    setFailed(false);
    try {
      await api.delete(`/chat/sessions/${session.id}`);
    } catch (err) {
      if ((err as Error & { status?: number }).status !== 404) {
        // 404=他端已删,视为已删照常收敛;其余失败保持对话框开启,提示可见
        setFailed(true);
        setBusy(false);
        return;
      }
    }
    setBusy(false);
    onOpenChange(false);
    // 已删会话的 messages 键不再 invalidate(重取只会 404);路由离开自然卸载
    await qc.invalidateQueries({ queryKey: ["sessions", current] });
    // 当前会话被删:视图回新建态(spec「删除会话需确认且即时收敛」)
    if (isActive) navigate("/chat", { replace: true });
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(o) => {
        // busy 期拦截一切关闭源(R1-S1):ESC/未来新增关闭路径统一收口——
        // 「再想想」disabled 只堵按钮,闸在此才堵全(spec「取消操作不可用」)
        if (!o && busy) return;
        onOpenChange(o);
        if (o) setFailed(false); // 重开清残留错误
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除此会话?</AlertDialogTitle>
          <AlertDialogDescription>
            「{session.title}」及其全部消息将被移除,此步不可挽回。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {failed && (
          <p className="text-sm text-destructive" role="alert">
            删除未成,请稍后再试。
          </p>
        )}
        <AlertDialogFooter>
          {/* busy 期禁用取消(013,010 挂账⑤):删除请求已发出,
              「再想想」可点会误导用户以为请求被撤回(spec「删除进行中取消不可用」) */}
          <AlertDialogCancel disabled={busy}>再想想</AlertDialogCancel>
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
