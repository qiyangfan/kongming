/* 管理区在宽屏使用左侧导航；窄屏显示管理索引页并进入各管理页面。 */

import { NavLink, Outlet, useLocation } from "react-router";
import { ChevronLeft } from "lucide-react";

import { ADMIN_NAV } from "@kongming/api-client/admin-nav";
import { useAuth } from "@kongming/api-client/auth-store";
import { hasPerm } from "@kongming/api-client/nav";
import { cn } from "@kongming/api-client/utils";

import { ThemeToggle } from "@/components/theme-toggle";

export default function AdminLayout() {
  const user = useAuth((s) => s.user);
  const visible = ADMIN_NAV.filter((item) => hasPerm(user, item.perm));
  const onIndex = useLocation().pathname === "/";

  return (
    <div className="flex h-full min-w-0 flex-col md:flex-row">
      {/* 窄屏顶栏承载品牌/返回入口与主题切换；宽屏导航与主题切换由侧栏承载。 */}
      <div className="flex items-center justify-between gap-1 border-b border-border px-3 py-2 md:hidden">
        {onIndex ? (
          <span className="px-2 text-sm font-semibold text-foreground">孔明管理台</span>
        ) : (
          <NavLink
            to="/"
            className="flex items-center gap-1 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-secondary/60 hover:text-foreground"
          >
            <ChevronLeft className="size-4" aria-hidden />
            管理索引
          </NavLink>
        )}
        <ThemeToggle />
      </div>
      <div className="hidden shrink-0 md:block md:w-48">
        <nav
          aria-label="管理导航"
          className="flex h-full flex-col items-stretch gap-0.5 overflow-y-auto border-r border-border px-3 py-5"
        >
          <div className="mb-2 flex items-center justify-between gap-2 px-3 py-1.5">
            <h1 className="text-lg font-semibold text-foreground">孔明管理台</h1>
            <ThemeToggle />
          </div>
          {visible.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex shrink-0 items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors",
                  isActive
                    ? "bg-secondary text-secondary-foreground font-medium"
                    : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                )
              }
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
      </div>
      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-4 md:px-6 md:py-5">
        <Outlet />
      </main>
    </div>
  );
}
