/* 登录页(铁律样板):基础组件全 shadcn/ui,色值全语义/品牌 token,
   品牌渐变为表现层(第 7 条豁免)但色值仍从 token 派生。
   联邦按钮数据驱动(/auth/options):未配置的 provider 不渲染(ADR-006)。 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, type OptionsOut, type StartOut, type TokenPairOut } from "../lib/api";
import { useAuth } from "../lib/auth-store";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/* 品牌官方 mark 内联(商标图标属铁律例外条款;003 五家齐发时迁 simple-icons) */
function GithubMark(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden {...props}>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

function GoogleMark(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden {...props}>
      <path
        fill="#4285F4"
        d="M23.49 12.27c0-.79-.07-1.54-.19-2.27H12v4.51h6.47c-.29 1.48-1.14 2.73-2.4 3.58v3h3.86c2.26-2.09 3.56-5.17 3.56-8.82Z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.86-3c-1.08.72-2.45 1.16-4.07 1.16-3.13 0-5.78-2.11-6.73-4.96H1.29v3.09C3.26 21.3 7.31 24 12 24Z"
      />
      <path
        fill="#FBBC05"
        d="M5.27 14.29c-.25-.72-.38-1.49-.38-2.29s.14-1.57.38-2.29V6.62H1.29C.47 8.24 0 10.06 0 12s.47 3.76 1.29 5.38l3.98-3.09Z"
      />
      <path
        fill="#EA4335"
        d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.7 1.29 6.62l3.98 3.09C6.22 6.86 8.87 4.75 12 4.75Z"
      />
    </svg>
  );
}

const PROVIDER_MARKS: Record<string, React.ComponentType<React.SVGProps<SVGSVGElement>>> = {
  github: GithubMark,
  google: GoogleMark,
};

export default function Login() {
  const navigate = useNavigate();
  const acceptTokens = useAuth((s) => s.acceptTokens);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [providers, setProviders] = useState<OptionsOut["providers"]>([]);

  useEffect(() => {
    // options 拉取失败不阻断账密登录(联邦入口整体隐藏)
    api
      .get<OptionsOut>("/auth/options")
      .then((o) => setProviders(o.providers))
      .catch(() => setProviders([]));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const pair = await api.post<TokenPairOut>("/auth/login", {
        username,
        password,
      });
      await acceptTokens(pair);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setPending(false);
    }
  }

  async function federatedLogin(provider: string) {
    setError(null);
    try {
      const { authorize_url } = await api.get<StartOut>(`/auth/${provider}/start`);
      window.location.href = authorize_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "联邦登录发起失败");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-grad-from via-grad-mid to-grad-end">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>孔明 Kongming</CardTitle>
          <CardDescription>问计于数据,如问计于孔明</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                placeholder="username"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                placeholder="••••••"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" disabled={pending} className="w-full">
              {pending ? "登录中…" : "登录"}
            </Button>
          </form>

          {providers.length > 0 && (
            <>
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span className="h-px flex-1 bg-border" />或
                <span className="h-px flex-1 bg-border" />
              </div>
              <div className="space-y-2">
                {providers.map((p) => {
                  const Mark = PROVIDER_MARKS[p.provider];
                  return (
                    <Button
                      key={p.provider}
                      variant="outline"
                      className="w-full"
                      onClick={() => void federatedLogin(p.provider)}
                    >
                      {Mark && <Mark />}
                      使用 {p.display_name} 继续
                    </Button>
                  );
                })}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
