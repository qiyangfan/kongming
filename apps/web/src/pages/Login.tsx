/* 登录页只展示统一身份(虎符)入口;未配置时提示联系运维。 */

import { useEffect, useState } from "react";
import { api, humanizeErrorText, type OptionsOut, type StartOut } from "../lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function Login() {
  const [providers, setProviders] = useState<OptionsOut["providers"]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    api
      .get<OptionsOut>("/auth/options")
      .then((o) => setProviders(o.providers))
      .catch(() => setProviders([]));
  }, []);

  async function federatedLogin(provider: string, displayName: string) {
    setPending(true);
    setError(null);
    try {
      const { authorize_url } = await api.get<StartOut>(`/auth/${provider}/start`);
      window.location.href = authorize_url;
    } catch (err) {
      setError(
        err instanceof Error
          ? humanizeErrorText(err.message)
          : `${displayName} 登录未完成,请稍后再试`,
      );
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-grad-from via-grad-mid to-grad-end">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle as="h2">孔明 Kongming</CardTitle>
          <CardDescription>问计于数据,如问计于孔明</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-muted-foreground text-sm">使用公司统一身份登录;孔明不保存密码。</p>
          {providers.length === 0 && (
            <p className="text-destructive text-sm" role="alert">
              统一身份未配置,请联系运维。
            </p>
          )}
          {providers.map((p) => (
            <Button
              key={p.provider}
              variant="outline"
              className="w-full"
              disabled={pending}
              onClick={() => void federatedLogin(p.provider, p.display_name)}
            >
              使用 {p.display_name} 登录
            </Button>
          ))}
          {error && (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
