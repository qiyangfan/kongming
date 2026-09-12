/* 品牌化错误页:403(无权限)与 404(未知路由),均带返回对话首页出口。 */

import { Compass, ShieldOff } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-store";
import { visibleNav } from "@/lib/nav";

function useHomePath(): string {
  const { user } = useAuth();
  return visibleNav(user)[0]?.to ?? "/login";
}

function ErrorShell({
  icon: Icon,
  title,
  hint,
}: {
  icon: typeof ShieldOff;
  title: string;
  hint: string;
}) {
  const homePath = useHomePath();
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center">
      <Icon className="size-12 text-primary" aria-hidden />
      <h1 className="text-2xl font-semibold tracking-wide text-foreground">{title}</h1>
      <p className="max-w-sm text-sm text-muted-foreground">{hint}</p>
      <Button asChild variant="outline" className="mt-2">
        <Link to={homePath}>回到对话</Link>
      </Button>
    </div>
  );
}

export function ForbiddenPage() {
  return (
    <ErrorShell
      icon={ShieldOff}
      title="此路不通"
      hint="当前账号没有进入此处的权限。如需开通,请联系管理员。"
    />
  );
}

export function NotFoundPage() {
  return (
    <ErrorShell icon={Compass} title="未见此处" hint="此地不在舆图之上。回到对话,继续问计。" />
  );
}
