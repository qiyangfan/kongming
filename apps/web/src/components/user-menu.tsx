/* 用户区(web-nav spec 应用壳):头像 + 下拉(身份概览/我的记忆/主题切换/登出)。 */

import { Brain, LogOut, Moon, Sun } from "lucide-react";
import { useNavigate } from "react-router";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth-store";
import { roleLabel } from "@/lib/role-labels";
import { useTheme } from "@/lib/theme-store";

export function UserMenu() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const name = user?.nickname || user?.username || "";
  const initial = name.slice(0, 1) || "?";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="w-full justify-start gap-2 px-2">
          <Avatar className="size-7">
            <AvatarFallback className="bg-primary text-xs text-primary-foreground">
              {initial}
            </AvatarFallback>
          </Avatar>
          <span className="min-w-0 flex-1 truncate text-left text-sm">{name}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span className="truncate">{name}</span>
          <span className="truncate text-xs font-normal text-muted-foreground">
            {user?.role_keys.length ? user.role_keys.map(roleLabel).join(" / ") : "无角色"}
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={toggle}>
          {theme === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
          {theme === "dark" ? "切换为亮色" : "切换为暗色"}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => navigate("/memory")}>
          <Brain aria-hidden />
          我的记忆
        </DropdownMenuItem>
        <DropdownMenuItem variant="destructive" onSelect={() => void logout()}>
          <LogOut aria-hidden />
          退出登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
