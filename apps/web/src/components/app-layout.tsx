/* 应用壳在宽屏显示会话侧栏，窄屏改用 BottomNav 与会话抽屉。 */

import { Link, Outlet } from "react-router";
import { Feather, Plus } from "lucide-react";

import { Protected } from "@/components/protected";
import { BottomNav } from "@/components/bottom-nav";
import { DatasourceSwitcher } from "@/components/datasource-switcher";
import { SessionSidebar } from "@/components/session-sidebar";
import { TenantSwitcher } from "@/components/tenant-switcher";
import { UserMenu } from "@/components/user-menu";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/lib/auth-store";
import { visibleNav } from "@/lib/nav";
import { useTenant } from "@/lib/tenant-store";
import { cn } from "@/lib/utils";

function BrandMark() {
  return (
    <Link to="/chat" className="flex items-center gap-2.5 px-1 py-1">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary">
        <Feather className="size-5 text-primary-foreground" aria-hidden />
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="text-base font-semibold tracking-widest text-foreground">孔明</span>
        <span className="truncate text-2xs text-muted-foreground">万事,问孔明。</span>
      </span>
    </Link>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: (() => void) | undefined }) {
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
        <DatasourceSwitcher />
        <UserMenu />
      </div>
    </div>
  );
}

export function AppLayout() {
  const { current } = useTenant();
  const user = useAuth((s) => s.user);
  // 安全区避让单源:顶部(pt-safe)/底部(pb-safe-nav)都在壳层消费,页面不自行
  // 感知;底部预留与 BottomNav 渲染条件同源,空态不渲染时不留幽灵避让带
  const showNav = visibleNav(user).length > 0;

  return (
    <Protected>
      {/* key 含租户:切库后整体重挂,消息区/滚动位不跨租户残留 */}
      <div key={current} className="flex h-dvh overflow-hidden bg-background">
        <aside className="hidden w-64 shrink-0 border-r border-border md:block">
          <SidebarBody />
        </aside>

        <div
          className={cn(
            "flex min-w-0 flex-1 flex-col pt-safe md:pt-0",
            showNav && "pb-safe-nav md:pb-0",
          )}
        >
          <main className="min-h-0 flex-1">
            <Outlet />
          </main>
        </div>

        <BottomNav />
      </div>
    </Protected>
  );
}
