/* 路由级权限守卫(web-nav spec「路由守卫分流」): 已认证但无权限点 → 403 页,不回退静默隐藏。 */

import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth-store";
import { hasPerm } from "@/lib/nav";
import { ForbiddenPage } from "@/components/error-pages";

export function PermRoute({ perm, children }: { perm: string; children: ReactNode }) {
  const { user } = useAuth();
  if (!hasPerm(user, perm)) return <ForbiddenPage />;
  return <>{children}</>;
}
