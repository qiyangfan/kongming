import * as React from "react";

import { cn } from "@kongming/api-client/utils";

/** 统一用户与助手消息气泡样式；左右对齐由调用方容器控制。 */
export function ChatBubble({
  variant,
  children,
  className,
}: {
  variant: "user" | "assistant";
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <p
      className={cn(
        "px-4 py-2 text-sm leading-relaxed",
        variant === "user"
          ? "max-w-[85%] rounded-2xl rounded-br-sm bg-primary text-primary-foreground"
          : "rounded-lg bg-muted/50 py-2.5 text-foreground",
        className,
      )}
    >
      {children}
    </p>
  );
}
