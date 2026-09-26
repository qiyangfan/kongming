/* 管理区导航配置单源(mobile-experience B2.2:自 AdminLayout 抽出)——
   AdminLayout 竖排子导航与窄屏管理索引页同一份数据、同一权限过滤;
   每项 perm 与对应路由 PermRoute 同一权限点(双源同构)。 */

import type { LucideIcon } from "lucide-react";
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

export type AdminNavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  /** 与对应路由 PermRoute 同一权限点(双源同构) */
  perm: string;
};

export const ADMIN_NAV: AdminNavItem[] = [
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
