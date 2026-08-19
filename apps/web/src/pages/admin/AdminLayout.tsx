/* 管理区布局(web-admin spec「管理面路由与页面守卫」):
   子页入口数据驱动(与路由守卫同源权限点)、按当前用户权限过滤、当前子页高亮;
   子页守卫由各路由的 PermRoute 承担(直达无权子页 → 403 页兜底)。 */

import { NavLink, Outlet } from "react-router";
import { Building2, ShieldCheck, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { useAuth } from "@/lib/auth-store";
import { hasPerm } from "@/lib/nav";
import { cn } from "@/lib/utils";

type AdminNavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  /** 与对应路由 PermRoute 同一权限点(双源同构) */
  perm: string;
};

const ADMIN_NAV: AdminNavItem[] = [
  { to: "/admin/users", label: "用户", icon: Users, perm: "system:user:manage" },
  { to: "/admin/roles", label: "角色", icon: ShieldCheck, perm: "system:role:manage" },
  { to: "/admin/depts", label: "部门", icon: Building2, perm: "system:user:manage" },
];

export default function AdminLayout() {
  const user = useAuth((s) => s.user);
  const visible = ADMIN_NAV.filter((item) => hasPerm(user, item.perm));

  return (
    <div className="flex h-full min-w-0 flex-col gap-4 px-6 py-5">
      <header className="flex items-center gap-1 border-b pb-3">
        <h1 className="mr-4 text-lg font-semibold text-foreground">管理</h1>
        {visible.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
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
      </header>
      <main className="min-h-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
