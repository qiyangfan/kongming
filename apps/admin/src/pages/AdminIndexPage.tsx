/* 窄屏访问 /admin 时按权限显示管理入口；宽屏继续打开默认管理页。 */

import { NavLink, Navigate } from "react-router";

import { useMediaQuery } from "@kongming/api-client/use-media-query";
import { ADMIN_NAV } from "@kongming/api-client/admin-nav";
import { useAuth } from "@kongming/api-client/auth-store";
import { hasPerm } from "@kongming/api-client/nav";
import { cn } from "@kongming/api-client/utils";

export default function AdminIndexPage() {
  const user = useAuth((s) => s.user);
  const visible = ADMIN_NAV.filter((item) => hasPerm(user, item.perm));
  const isWide = useMediaQuery("(min-width: 768px)");

  // 窄屏无任何可见板块时同样重定向:交由 /users 的 PermRoute 落 403
  // 兜底页,与宽屏同态,避免无权限者看到一张空索引卡
  if (isWide || visible.length === 0) {
    return <Navigate to="/users" replace />;
  }

  return (
    <div className="mx-auto max-w-3xl p-4">
      <h1 className="text-lg font-semibold text-foreground">管理</h1>
      <p className="mt-1 text-sm text-muted-foreground">选择要管理的板块</p>
      <nav aria-label="管理索引" className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
        {visible.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm transition-colors",
                isActive
                  ? "border-ring bg-secondary font-medium text-secondary-foreground"
                  : "text-foreground hover:bg-secondary/60",
              )
            }
          >
            <Icon className="size-5 shrink-0 text-muted-foreground" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
