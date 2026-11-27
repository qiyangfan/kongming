/* 管理台登录:独立 Keycloak client(kongming-admin-web),不回退用户会话。 */

import { useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";

import { api } from "@kongming/api-client/api";
import type { components } from "@kongming/api-client/api-types.gen";
import { Button } from "@kongming/ui/button";
import { ErrorAlert } from "@kongming/ui/error-alert";

type StartOut = components["schemas"]["StartOut"];

export default function Login() {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function start(): Promise<void> {
    setPending(true);
    setError(null);
    try {
      const out = await api.get<StartOut>("/admin-auth/keycloak/start");
      window.location.href = out.authorize_url;
    } catch {
      setError("统一身份不可用或管理台未配置,请联系运维检查 Keycloak client。");
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm rounded-xl border border-border bg-card p-8 shadow-sm">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-6 text-primary" aria-hidden />
          <h1 className="text-xl font-semibold text-foreground">孔明管理台</h1>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">
          服务域配置与运行时管理;共享控制只读,登录要求 kongming-admin 角色。
        </p>
        {error && (
          <div className="mt-4">
            <ErrorAlert text={error} />
          </div>
        )}
        <Button className="mt-6 w-full" onClick={() => void start()} disabled={pending}>
          {pending && <Loader2 className="size-4 animate-spin" aria-hidden />}
          使用统一身份登录
        </Button>
      </div>
    </div>
  );
}
