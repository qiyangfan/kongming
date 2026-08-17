/* "/" 与路由守卫:未认证→/login;首登改密→/change-password;
   占位首页只证认证闭环(主布局=会话侧栏+对话区,属后续变更)。 */

import { useEffect } from "react";
import { Navigate } from "react-router";
import { Loader2, LogOut, ShieldCheck, UserRound } from "lucide-react";
import { useAuth } from "./lib/auth-store";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function Protected({
  children,
  allowMustChange = false,
}: {
  children: React.ReactNode;
  allowMustChange?: boolean;
}) {
  const { status, user, bootstrap } = useAuth();

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
  if (!allowMustChange && user?.must_change_password) {
    return <Navigate to="/change-password" replace />;
  }
  return <>{children}</>;
}

export default function App() {
  const { user, logout } = useAuth();

  return (
    <Protected>
      <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-grad-from via-grad-mid to-grad-end">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <UserRound className="size-5 text-primary" aria-hidden />
              {user?.nickname || user?.username}
            </CardTitle>
            <CardDescription>万事,问孔明。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2 text-sm text-muted-foreground">
              <p className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-accent" aria-hidden />
                角色:{user?.role_keys.length ? user.role_keys.join(" / ") : "无"}
              </p>
              <p>权限点:{user?.perm_codes.length ? user.perm_codes.join("、") : "无"}</p>
              <p>
                可切换租户:
                {user?.tenants.length
                  ? user.tenants.map((t) => t.tenant_name).join("、")
                  : "无"}
              </p>
            </div>
            <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
              认证闭环已通:主布局(会话侧栏 + 对话区)将在后续变更交付。
            </p>
            <Button variant="outline" className="w-full" onClick={() => void logout()}>
              <LogOut aria-hidden />
              退出登录
            </Button>
          </CardContent>
        </Card>
      </div>
    </Protected>
  );
}
