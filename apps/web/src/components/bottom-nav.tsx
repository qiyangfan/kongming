/* 底部标签栏(mobile-experience B2.1):窄屏能力导航唯一承载——
   与宽屏侧栏同一 visibleNav() 单源(含 RBAC 权限过滤),当前能力高亮;
   md+ 不渲染(宽屏侧栏常驻)。结构:外层吃底部安全区(pb-safe)、内层行
   固定 h-16,总高恰为 4rem + 安全区,与壳层 pb-safe-nav 预留精确对齐
   (安全区 padding 与行高不同元素,互不挤占触控内容区)。 */

import { NavLink } from "react-router";

import { useAuth } from "@/lib/auth-store";
import { visibleNav } from "@/lib/nav";
import { cn } from "@/lib/utils";

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
