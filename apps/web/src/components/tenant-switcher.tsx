/* 多租户模式允许切换工作空间，单租户只读展示。用户界面用“工作空间”，管理界面仍称“租户”。 */

import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronsUpDown } from "lucide-react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useTenant } from "@/lib/tenant-store";

export function TenantSwitcher() {
  const { current, available, switchTo } = useTenant();
  const qc = useQueryClient();
  const navigate = useNavigate();

  if (available.length <= 1) {
    return (
      <div className="flex h-8 items-center gap-2 px-2 text-xs text-muted-foreground">
        <span className="text-muted-foreground/70">工作空间</span>
        <span className="truncate font-medium text-foreground">
          {available[0]?.tenant_name ?? current ?? "—"}
        </span>
      </div>
    );
  }

  function change(key: string): void {
    if (key === current) return;
    switchTo(key);
    // 先离场再失效:旧会话消息请求若在失效后仍飞,会带新租户头撞 404 空转
    void navigate("/chat", { replace: true });
    void qc.invalidateQueries({ queryKey: ["sessions"] });
    qc.removeQueries({ queryKey: ["messages"] });
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="w-full justify-between">
          <span className="truncate">
            <span className="text-muted-foreground">工作空间</span>
            <span className="ml-1.5 font-medium">{current ?? "—"}</span>
          </span>
          <ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      {/* 面板宽度随触发器(原生 select 观感,Radix 锚宽变量),内容超长仍可撑宽 */}
      <DropdownMenuContent align="start" className="min-w-(--radix-popper-anchor-width)">
        <DropdownMenuLabel>切换工作空间</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {available.map((t) => (
          <DropdownMenuItem key={t.tenant_key} onSelect={() => change(t.tenant_key)}>
            <Check
              className={"size-4 " + (t.tenant_key === current ? "opacity-100" : "opacity-0")}
              aria-hidden
            />
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{t.tenant_name}</span>
              <span className="truncate text-xs text-muted-foreground">{t.tenant_key}</span>
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
