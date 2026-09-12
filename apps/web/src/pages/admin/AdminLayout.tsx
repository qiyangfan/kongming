/* 管理区布局(web-admin spec「管理面路由与页面守卫」):
   子页入口数据驱动(与路由守卫同源权限点)、按当前用户权限过滤、当前子页高亮;
   子页守卫由各路由的 PermRoute 承担(直达无权子页 → 403 页兜底)。
   子导航 md+ 左侧竖排(入口多且持续增长,横排放不下)、md 以下顶部横向滚动;
   每项 shrink-0,禁止 flex 压缩导致文字竖排换行;窄屏横滚时缘部渐隐提示更多入口。 */

import { useRef } from "react";
import { NavLink, Outlet } from "react-router";
import {
  Activity,
  BookOpen,
  Bot,
  Cable,
  Receipt,
  Workflow,
  Zap,
  UsersRound,
  Building2,
  Database,
  Layers,
  MessageSquareHeart,
  ScrollText,
  ShieldCheck,
  ShieldAlert,
  Users,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { FadeEdges, useScrollableX } from "@/components/scroll-fade";
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
  { to: "/admin/datasources", label: "数据源", icon: Database, perm: "system:datasource:manage" },
  { to: "/admin/rag", label: "知识库", icon: BookOpen, perm: "system:rag:manage" },
  { to: "/admin/feedback", label: "反馈", icon: MessageSquareHeart, perm: "system:feedback:view" },
  { to: "/admin/audit", label: "审计", icon: ScrollText, perm: "system:audit:view" },
  { to: "/admin/tools", label: "工具", icon: Wrench, perm: "system:tool:manage" },
  { to: "/admin/guardrails", label: "护栏", icon: ShieldAlert, perm: "system:guardrail:manage" },
  { to: "/admin/semantic", label: "语义层", icon: Layers, perm: "system:semantic:manage" },
  { to: "/admin/mcp", label: "MCP", icon: Cable, perm: "system:mcp:manage" },
  { to: "/admin/workflow", label: "工作流", icon: Workflow, perm: "system:workflow:manage" },
  { to: "/admin/triggers", label: "触发器", icon: Zap, perm: "system:trigger:manage" },
  { to: "/admin/teams", label: "团队", icon: UsersRound, perm: "system:team:manage" },
  { to: "/admin/billing", label: "计费", icon: Receipt, perm: "system:billing:manage" },
  { to: "/admin/bots", label: "助手", icon: Bot, perm: "chat:execute" },
  { to: "/admin/usage", label: "用量", icon: Activity, perm: "system:llm-usage:view" },
];

export default function AdminLayout() {
  const user = useAuth((s) => s.user);
  const visible = ADMIN_NAV.filter((item) => hasPerm(user, item.perm));
  // 窄屏横滚缘渐隐(走查 P1):提示右侧还有入口,滚到尽头自动消失
  const navRef = useRef<HTMLElement>(null);
  const navEdge = useScrollableX(navRef);

  return (
    <div className="flex h-full min-w-0 flex-col md:flex-row">
      <div className="relative shrink-0 md:w-44">
        <nav
          ref={navRef}
          aria-label="管理导航"
          className="flex h-full items-center gap-1 overflow-x-auto border-b border-border px-4 py-3 md:flex-col md:items-stretch md:overflow-y-auto md:border-b-0 md:border-r md:px-3 md:py-5"
        >
          <h1 className="mr-3 shrink-0 text-lg font-semibold text-foreground md:mb-2 md:mr-0 md:px-3 md:py-1.5">
            管理
          </h1>
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
        <FadeEdges {...navEdge} />
      </div>
      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto px-6 py-5">
        <Outlet />
      </main>
    </div>
  );
}
