/* 管理侧栏和窄屏索引共用导航项及权限过滤规则。 */

import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BookOpen,
  Bot,
  Cable,
  PlugZap,
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

type AdminNavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  /** 与对应路由 PermRoute 同一权限点(双源同构) */
  perm: string;
};

export const ADMIN_NAV: AdminNavItem[] = [
  { to: "/users", label: "用户", icon: Users, perm: "system:user:manage" },
  {
    to: "/roles",
    label: "角色",
    icon: ShieldCheck,
    perm: "system:role:manage",
  },
  { to: "/depts", label: "部门", icon: Building2, perm: "system:user:manage" },
  {
    to: "/datasources",
    label: "数据源",
    icon: Database,
    perm: "system:datasource:manage",
  },
  { to: "/rag", label: "知识库", icon: BookOpen, perm: "system:rag:manage" },
  {
    to: "/feedback",
    label: "反馈",
    icon: MessageSquareHeart,
    perm: "system:feedback:view",
  },
  { to: "/audit", label: "审计", icon: ScrollText, perm: "system:audit:view" },
  { to: "/tools", label: "工具", icon: Wrench, perm: "system:tool:manage" },
  {
    to: "/guardrails",
    label: "护栏",
    icon: ShieldAlert,
    perm: "system:guardrail:manage",
  },
  {
    to: "/semantic",
    label: "语义层",
    icon: Layers,
    perm: "system:semantic:manage",
  },
  { to: "/mcp", label: "MCP", icon: Cable, perm: "system:mcp:manage" },
  {
    to: "/connectors",
    label: "连接器",
    icon: PlugZap,
    perm: "connector:manage",
  },
  {
    to: "/workflow",
    label: "工作流",
    icon: Workflow,
    perm: "system:workflow:manage",
  },
  {
    to: "/triggers",
    label: "触发器",
    icon: Zap,
    perm: "system:trigger:manage",
  },
  { to: "/teams", label: "团队", icon: UsersRound, perm: "system:team:manage" },
  {
    to: "/control",
    label: "控制面",
    icon: Activity,
    perm: "system:control:view",
  },
  { to: "/bots", label: "助手", icon: Bot, perm: "chat:execute" },
  {
    to: "/usage",
    label: "用量",
    icon: Activity,
    perm: "system:llm-usage:view",
  },
  {
    to: "/quality",
    label: "问答质量",
    icon: Activity,
    perm: "system:chat-quality:view",
  },
];
