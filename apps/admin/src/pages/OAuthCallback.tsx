/* 管理台 OIDC 回调:换本地会话并回到管理台;缺粗角色明确提示。 */

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { useAuth } from "@kongming/api-client/auth-store";
import { ErrorAlert } from "@kongming/ui/error-alert";

export default function OAuthCallback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const acceptTokens = useAuth((s) => s.acceptTokens);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = params.get("code") ?? "";
    const state = params.get("state") ?? "";
    if (!code || !state) {
      setError("回调缺少 code/state,请重新登录。");
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const resp = await fetch("/api/admin-auth/keycloak/callback", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code, state }),
        });
        if (!resp.ok) {
          const detail =
            resp.status === 403
              ? "当前账号缺少管理台角色(kongming-admin),请联系管理员授权。"
              : "登录失败,请重新发起登录。";
          throw new Error(detail);
        }
        const pair = (await resp.json()) as {
          access_token: string;
          refresh_token: string;
          token_type?: string;
        };
        await acceptTokens({ ...pair, token_type: pair.token_type ?? "Bearer" });
        if (!cancelled) void navigate("/", { replace: true });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "登录失败");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params, acceptTokens, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        {error ? <ErrorAlert text={error} /> : <p className="text-center text-sm">正在完成登录…</p>}
      </div>
    </div>
  );
}
