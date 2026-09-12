import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * 聊天气泡(全站单源):role 定形态——user=主色底/直角右下,assistant=muted 底/圆角;
 * 左右对齐由调用处容器负责(flex justify-end / items-end),气泡统一
 * px-4 py-2 text-sm leading-relaxed(宪法 Article 6,消灭各页复制与 leading 漂移)。
 */
export function ChatBubble({
  role,
  children,
  className,
}: {
  role: "user" | "assistant";
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <p
      className={cn(
        "px-4 py-2 text-sm leading-relaxed",
        role === "user"
          ? "max-w-[85%] rounded-2xl rounded-br-sm bg-primary text-primary-foreground"
          : "rounded-lg bg-muted/50 py-2.5 text-foreground",
        className,
      )}
    >
      {children}
    </p>
  );
}
