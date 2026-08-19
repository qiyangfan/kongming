/* 应用壳(web-nav spec「应用壳结构」+「响应式外壳」):
   侧栏(品牌区/新对话/会话列表/导航区/用户区)+ 内容区 Outlet;
   md 以下侧栏收抽屉。认证分流由 Protected 承担,权限分流由子路由 PermRoute 承担。 */

import { useState } from "react";
import { Link, Outlet } from "react-router";
import { Feather, Menu, Plus } from "lucide-react";

import { Protected } from "@/components/protected";
import { SessionSidebar } from "@/components/session-sidebar";
import { TenantSwitcher } from "@/components/tenant-switcher";
import { UserMenu } from "@/components/user-menu";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useAuth } from "@/lib/auth-store";
import { visibleNav } from "@/lib/nav";
import { useTenant } from "@/lib/tenant-store";

function BrandMark() {
  return (
    <Link to="/chat" className="flex items-center gap-2.5 px-1 py-1">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary">
        <Feather className="size-5 text-primary-foreground" aria-hidden />
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="text-base font-semibold tracking-widest text-foreground">孔明</span>
        <span className="truncate text-[11px] text-muted-foreground">万事,问孔明。</span>
      </span>
    </Link>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const items = visibleNav(user);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="px-1">
        <BrandMark />
      </div>

      <Button asChild variant="default" className="w-full justify-start">
        <Link to="/chat" onClick={onNavigate}>
          <Plus aria-hidden />
          新对话
        </Link>
      </Button>

      <SessionSidebar onNavigate={onNavigate} />

      {items.length > 0 && (
        <>
          <Separator />
          <nav aria-label="主导航" className="flex flex-col gap-1">
            {items.map((item) => (
              <Button
                key={item.id}
                asChild
                variant="ghost"
                className="w-full justify-start text-muted-foreground"
              >
                <Link to={item.to} onClick={onNavigate}>
                  <item.icon aria-hidden />
                  {item.label}
                </Link>
              </Button>
            ))}
          </nav>
        </>
      )}

      <div className="mt-auto flex flex-col gap-2">
        <Separator />
        <TenantSwitcher />
        <UserMenu />
      </div>
    </div>
  );
}

export function AppLayout() {
  const [open, setOpen] = useState(false);
  const { current } = useTenant();

  return (
    <Protected>
      {/* key 含租户:切库后整体重挂,消息区/滚动位不跨租户残留 */}
      <div key={current} className="flex h-dvh overflow-hidden bg-background">
        <aside className="hidden w-64 shrink-0 border-r border-border md:block">
          <SidebarBody />
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-2 border-b border-border px-3 py-2 md:hidden">
            <Sheet open={open} onOpenChange={setOpen}>
              <SheetTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="打开菜单">
                  <Menu aria-hidden />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" showCloseButton={false} className="w-72 p-0">
                <SheetTitle className="sr-only">导航菜单</SheetTitle>
                <SidebarBody onNavigate={() => setOpen(false)} />
              </SheetContent>
            </Sheet>
            <span className="text-sm font-medium tracking-widest text-foreground">孔明</span>
          </header>

          <main className="min-h-0 flex-1">
            <Outlet />
          </main>
        </div>
      </div>
    </Protected>
  );
}
