/* 会话抽屉(mobile-experience B2.3):窄屏会话列表唯一出口。
   内容仅会话列表 + 新建对话——主导航不进抽屉(归 BottomNav 承载);
   会话交互与宽屏侧栏同源(同一 SessionSidebar 单源,新建/切换/删除一致)。
   触发入口由对话区页头放置(ChatPage md:hidden 页头)。 */

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
