/* 窄屏会话列表由此抽屉承载；主导航仍由 BottomNav 提供，入口位于对话页头。 */

import { useState } from "react";
import { Link } from "react-router";
import { History, Plus } from "lucide-react";

import { SessionSidebar } from "@/components/session-sidebar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export function SessionDrawer() {
  const [open, setOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="会话列表">
          <History aria-hidden />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" showCloseButton={false} className="w-72 p-0">
        <SheetTitle className="sr-only">会话列表</SheetTitle>
        {/* portal 浮层不经过壳层内容列,须自行避让顶部安全区 */}
        <div className="flex h-full min-h-0 flex-col gap-3 p-3 pt-safe">
          <Button asChild variant="default" className="w-full justify-start">
            <Link to="/chat" onClick={() => setOpen(false)}>
              <Plus aria-hidden />
              新对话
            </Link>
          </Button>
          <SessionSidebar onNavigate={() => setOpen(false)} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
