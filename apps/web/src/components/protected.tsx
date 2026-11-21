/* 认证态守卫:loading → spinner;guest → /login。会话恢复单飞幂等(auth-store bootstrap)。 */

import { useEffect, type ReactNode } from "react";
import { Navigate } from "react-router";
import { Loader2 } from "lucide-react";

import { useAuth } from "@/lib/auth-store";

export function Protected({ children }: { children: ReactNode }) {
  const { status, bootstrap } = useAuth();

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-label="加载中" />
      </div>
    );
  }
  if (status === "guest") return <Navigate to="/login" replace />;
  return <>{children}</>;
}
