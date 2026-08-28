/* 数据源切换器(008 datasource spec「对话页数据源切换器」,与租户切换器同构):
   选项接口驱动(首位合成 default 项);选中 key 随 /chat/query 发送;
   切租户自动重置首项(options 按租户取,数据变更即 recalibrate);
   切换=回新建态+失效会话域查询。
   注意(挂账):会话列表不按数据源过滤,直达旧会话追问将按当前源执行——
   会话-数据源绑定语义待 chat 域立约,本组件不作跨源隔离承诺。 */

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import { datasourceApi } from "@/lib/datasource-api";
import { useDatasource } from "@/lib/datasource-store";
import { useTenant } from "@/lib/tenant-store";

export function DatasourceSwitcher() {
  const tenant = useTenant((s) => s.current);
  const { current, switchTo, calibrate } = useDatasource();
  const qc = useQueryClient();
  const navigate = useNavigate();

  const options = useQuery({
    queryKey: ["datasource-options", tenant],
    queryFn: datasourceApi.options,
    enabled: tenant !== null,
  });

  // 选项就绪即校准:持久值失效(切租户/源被禁删)回落首项
  useEffect(() => {
    if (options.data) calibrate(options.data.map((o) => o.datasource_key));
  }, [options.data, calibrate]);

  const items = options.data ?? [];
  const currentName = items.find((o) => o.datasource_key === current)?.name ?? "默认数据源";

  // 拉取失败(无租户绑定 403/网络错误):显式错误态,不误标当前选择(R1-web-O4)
  if (options.isError) {
    return (
      <div className="flex h-8 items-center gap-2 px-2 text-xs text-muted-foreground">
        <span className="text-muted-foreground/70">数据源</span>
        <span className="truncate text-destructive">选项加载失败</span>
      </div>
    );
  }

  if (items.length <= 1) {
    return (
      <div className="flex h-8 items-center gap-2 px-2 text-xs text-muted-foreground">
        <span className="text-muted-foreground/70">数据源</span>
        <span className="truncate font-medium text-foreground">{currentName}</span>
      </div>
    );
  }

  function change(key: string): void {
    if (key === current) return;
    switchTo(key);
    // 先离场再失效(与租户切换同构):旧请求带新源选择会语义错位
    navigate("/chat", { replace: true });
    void qc.invalidateQueries({ queryKey: ["sessions"] });
    void qc.removeQueries({ queryKey: ["messages"] });
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="w-full justify-between">
          <span className="truncate">
            <span className="text-muted-foreground">数据源</span>
            <span className="ml-1.5 font-medium">{currentName}</span>
          </span>
          <ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      {/* 面板宽度随触发器(原生 select 观感,Radix 锚宽变量),内容超长仍可撑宽 */}
      <DropdownMenuContent align="start" className="min-w-(--radix-popper-anchor-width)">
        <DropdownMenuLabel>切换数据源</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {items.map((o) => (
          <DropdownMenuItem key={o.datasource_key} onSelect={() => change(o.datasource_key)}>
            <Check
              className={"size-4 " + (o.datasource_key === current ? "opacity-100" : "opacity-0")}
              aria-hidden
            />
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{o.name}</span>
              <span className="truncate text-xs text-muted-foreground">{o.datasource_key}</span>
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
