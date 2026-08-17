/* 首登强制改密 / 自助改密页:改密成功即吊销全部 refresh 家族(后端),
   本地清态回登录页重新登录。 */

import { useState } from "react";
import { useNavigate } from "react-router";
import { Protected } from "../App";
import { api, clearTokens } from "../lib/api";
import { useAuth } from "../lib/auth-store";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function ChangePasswordForm() {
  const navigate = useNavigate();
  const user = useAuth((s) => s.user);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (next !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await api.post("/auth/password/change", {
        current_password: current,
        new_password: next,
      });
      // 后端已吊销全部会话(含当前),清态(联动 store 回落 guest)后重新登录
      clearTokens();
      navigate("/login", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "改密失败");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-grad-from via-grad-mid to-grad-end">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>修改密码</CardTitle>
          <CardDescription>
            {user?.must_change_password
              ? "首次登录须先修改初始密码"
              : "修改后所有设备需重新登录"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="current">当前密码</Label>
              <Input
                id="current"
                type="password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="next">新密码</Label>
              <Input
                id="next"
                type="password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm">确认新密码</Label>
              <Input
                id="confirm"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" disabled={pending} className="w-full">
              {pending ? "提交中…" : "确认修改"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

export default function ChangePassword() {
  return (
    <Protected allowMustChange>
      <ChangePasswordForm />
    </Protected>
  );
}
