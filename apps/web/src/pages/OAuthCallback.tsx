/* 联邦登录回调只将 code 和 state 发给后端；OAuth 令牌不经过浏览器，state 仅处理一次。 */

import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { Loader2 } from "lucide-react";
import { api, humanizeErrorText, type TokenPairOut } from "@kongming/api-client/api";
import { useAuth } from "@kongming/api-client/auth-store";
import { buttonVariants } from "@kongming/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@kongming/ui/card";
import { cn } from "@kongming/api-client/utils";

export default function OAuthCallback() {
  const { provider } = useParams<{ provider: string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const acceptTokens = useAuth((s) => s.acceptTokens);
  const [error, setError] = useState<string | null>(null);
  const called = useRef(false);

  useEffect(() => {
    if (called.current) return;
    called.current = true;
    const code = params.get("code");
    const state = params.get("state");
    if (!provider || !code || !state) {
      setError("登录未完成,请返回登录页重试");
      return;
    }
    void (async () => {
      try {
        const pair = await api.post<TokenPairOut>(`/auth/${provider}/callback`, { code, state });
        await acceptTokens(pair);
        void navigate("/", { replace: true });
      } catch (err) {
        setError(err instanceof Error ? humanizeErrorText(err.message) : "登录未完成,请稍后再试");
      }
    })();
  }, [provider, params, acceptTokens, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-grad-from via-grad-mid to-grad-end">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle as="h2">第三方登录</CardTitle>
          <CardDescription>{error ? "登录未完成" : "正在完成登录…"}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error ? (
            <>
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
              <Link to="/login" className={cn(buttonVariants({ variant: "outline" }), "w-full")}>
                返回登录页
              </Link>
            </>
          ) : (
            <div className="flex justify-center py-2">
              <Loader2 className="size-6 animate-spin text-muted-foreground" aria-label="登录中" />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
