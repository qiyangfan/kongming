/* 管理台亮暗切换:标签呈现切换目标态,偏好写入 theme-store 并即时挂到根元素。 */

import { Moon, Sun } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { useTheme } from "@kongming/api-client/theme-store";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const label = theme === "dark" ? "切换为亮色" : "切换为暗色";

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={toggle}
      aria-label={label}
      title={label}
    >
      {theme === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
    </Button>
  );
}
