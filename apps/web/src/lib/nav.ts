/* 导航配置单源(web-nav spec「应用壳结构」「RBAC 菜单守卫」):
   菜单可见性过滤与路由级守卫同源消费,新增能力域入口=加一行配置,零改壳代码。 */

import type { LucideIcon } from "lucide-react";
import { MessageSquare, Store, KeyRound, Settings2 } from "lucide-react";

import type { MeOut } from "./api";

export interface NavItem {
  id: string;
  label: string;
  icon: LucideIcon;
  to: string;
  /** 进入所需权限点;菜单与路由守卫共用同一判定 */
  perm: string;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "chat", label: "对话", icon: MessageSquare, to: "/chat", perm: "chat:execute" },
  { id: "store", label: "商店", icon: Store, to: "/store", perm: "bot:run" },
  { id: "portal", label: "门户", icon: KeyRound, to: "/portal", perm: "developer:portal" },
  { id: "admin", label: "管理", icon: Settings2, to: "/admin", perm: "system:user:manage" },
];

export function hasPerm(user: MeOut | null, code: string): boolean {
  return user?.perm_codes.includes(code) ?? false;
}

export function visibleNav(user: MeOut | null): NavItem[] {
  return NAV_ITEMS.filter((item) => hasPerm(user, item.perm));
}
