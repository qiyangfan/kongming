/* 窄屏能力导航与宽屏共享权限过滤后的入口；底部安全区由外层容器处理。 */

import { NavLink } from "react-router";

import { useAuth } from "@kongming/api-client/auth-store";
import { visibleNav } from "@kongming/api-client/nav";
import { cn } from "@kongming/api-client/utils";

export function BottomNav() {
  const { user } = useAuth();
  const items = visibleNav(user);
  // 无任何能力权限(理论不达):不渲染空条(壳层底部避让与此条件同源)
  if (items.length === 0) return null;

  return (
    <nav aria-label="能力导航" className="fixed inset-x-0 bottom-0 z-40 pb-safe md:hidden">
      <div className="flex h-16 items-stretch border-t border-border bg-background">
        {items.map((item) => (
          <NavLink
            key={item.id}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "flex min-w-0 flex-1 flex-col items-center justify-center gap-1 text-xs",
                isActive
                  ? "font-medium text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )
            }
          >
            <item.icon className="size-5" aria-hidden />
            <span className="truncate px-1">{item.label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
