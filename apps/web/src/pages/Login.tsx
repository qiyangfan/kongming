/* 登录页即前端普通路由(ADR-003):
   与主应用共用同一套 Tailwind token 与组件(shadcn/ui 初始化后替换原生元素),
   不再镜像 Keycloak/PatternFly 的第二套样式体系。 */

import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../lib/api";

export default function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      await api.post("/auth/login", { username, password });
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setPending(false);
    }
  }

  async function githubLogin() {
    const { authorize_url } = await api.get<{ authorize_url: string }>("/auth/github/start");
    window.location.href = authorize_url;
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-brand-500 via-teal-500 to-cyan-600">
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-xl">
        <h1 className="mb-1 text-2xl font-semibold text-slate-900">孔明 Kongming</h1>
        <p className="mb-6 text-sm text-slate-500">问计于数据,如问计于孔明</p>

        <form onSubmit={submit} className="space-y-4">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="用户名"
            autoComplete="username"
            className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-50"
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="密码"
            autoComplete="current-password"
            className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-50"
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={pending}
            className="w-full rounded-lg bg-brand-600 py-2.5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-50"
          >
            {pending ? "登录中…" : "登录"}
          </button>
        </form>

        <div className="my-5 flex items-center gap-3 text-xs text-slate-400">
          <span className="h-px flex-1 bg-slate-100" />或<span className="h-px flex-1 bg-slate-100" />
        </div>
        <button
          onClick={githubLogin}
          className="w-full rounded-lg border border-slate-200 py-2.5 text-sm text-slate-700 transition hover:bg-slate-50"
        >
          使用 GitHub 继续
        </button>
      </div>
    </div>
  );
}
