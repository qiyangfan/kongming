/* Bot 公开嵌入页(025,custom-agent spec「发布渠道」):免登录单轮问答;
   Bot 未公开/不存在时后端 404,前端呈现统一不可用文案(不暴露存在性)。 */

import { useState } from "react";
import { Loader2, SendHorizontal } from "lucide-react";
import { useParams } from "react-router";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { components } from "@/lib/api-types.gen";

// ADR-005:消费生成契约(审查循环 F1:手写接口使 tsc 对后端字段漂移零感知)
type BotChatReply = components["schemas"]["BotChatOut"];

export default function BotPublicPage() {
  const { botKey } = useParams<{ botKey: string }>();
  const [draft, setDraft] = useState("");
  const [asking, setAsking] = useState(false);
  const [rounds, setRounds] = useState<{ q: string; a: string }[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    const q = draft.trim();
    if (!q || asking) return;
    setAsking(true);
    setError(null);
    try {
      const resp = await fetch(`/api/bot/${botKey}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      if (resp.status === 404) {
        setError("该助手不存在或未公开。");
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as BotChatReply;
      setRounds((prev) => [...prev, { q, a: data.summary || data.error || "无答复" }]);
      setDraft("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "服务异常,请稍后重试");
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col px-4 py-6">
      <header className="mb-4 text-center">
        <h1 className="text-lg font-semibold tracking-wide">智能助手</h1>
        <p className="text-xs text-muted-foreground">万事,问孔明。</p>
      </header>
      <div className="flex-1 space-y-4 overflow-y-auto">
        {rounds.length === 0 && (
          <p className="pt-16 text-center text-sm text-muted-foreground">
            直接提问即可
          </p>
        )}
        {rounds.map((r, i) => (
          <div key={i} className="space-y-2">
            <div className="flex justify-end">
              <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm text-primary-foreground">
                {r.q}
              </p>
            </div>
            <p className="rounded-lg bg-muted/50 px-4 py-2.5 text-sm leading-relaxed">{r.a}</p>
          </div>
        ))}
        {error && (
          <p className="text-sm text-destructive" role="alert">{error}</p>
        )}
      </div>
      <div className="mt-4 flex items-end gap-2">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask();
            }
          }}
          placeholder="输入你的问题…"
          rows={1}
          className="max-h-32 min-h-10 resize-none"
          aria-label="提问"
        />
        <Button size="icon" disabled={!draft.trim() || asking} onClick={() => void ask()} aria-label="发送">
          {asking ? <Loader2 className="animate-spin" aria-hidden /> : <SendHorizontal aria-hidden />}
        </Button>
      </div>
    </div>
  );
}
