/* 对话滚动区：记录近底跟随，切换会话强制贴底，回到底部浮标联动。 */

import { useEffect, useRef, useState } from "react";

import { isNearBottom } from "@/lib/scroll-follow";

export interface ChatScrollApi {
  scrollRootRef: React.RefObject<HTMLDivElement | null>;
  viewportRef: React.RefObject<HTMLDivElement | null>;
  followBottom: boolean;
  setFollowBottom: (near: boolean) => void;
  scrollToBottom: (behavior: ScrollBehavior) => void;
}

/** 视口挂载后取一次滚动容器，监听滚动翻转 followBottom。 */
export function useChatScroll(): ChatScrollApi {
  // 滚动容器视口引用 + 近底跟随状态。视口经 ScrollArea Root 探
  // data-slot 取得(不改双仓标准件 ui/scroll-area.tsx;Radix 隐藏原生滚动条,
  // 判定不能挂 window)
  const scrollRootRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [followBottom, setFollowBottom] = useState(true);

  // 视口引用挂载后取一次(ScrollArea 常驻,分支切换不卸载)
  useEffect(() => {
    const root = scrollRootRef.current;
    if (!root) return;
    viewportRef.current = root.querySelector<HTMLDivElement>('[data-slot="scroll-area-viewport"]');
  }, []);

  // 滚动监听只翻转布尔(状态更新限于变化帧,防每帧 setState)
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onScroll = () => {
      const near = isNearBottom({
        scrollTop: el.scrollTop,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
      });
      setFollowBottom((prev) => (prev === near ? prev : near));
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  /** 滚到最底部（followBottom 写入由调用方负责）。 */
  function scrollToBottom(behavior: ScrollBehavior): void {
    const el = viewportRef.current;
    el?.scrollTo({ top: el.scrollHeight, behavior });
  }

  return { scrollRootRef, viewportRef, followBottom, setFollowBottom, scrollToBottom };
}

/** 内容变化后跟随贴底：近底才跟随，强制标记时立即贴底。 */
export function useFollowBottom(follow: {
  viewportRef: React.RefObject<HTMLDivElement | null>;
  followBottom: boolean;
  forceBottomRef: React.RefObject<boolean>;
  deps: unknown[];
}): void {
  const { viewportRef, followBottom, forceBottomRef, deps } = follow;
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const force = forceBottomRef.current;
    forceBottomRef.current = false;
    if (!followBottom && !force) return;
    let inner = 0;
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => {
        el.scrollTo({ top: el.scrollHeight, behavior: force ? "auto" : "smooth" });
      });
    });
    return () => {
      cancelAnimationFrame(outer);
      cancelAnimationFrame(inner);
    };
    // 依赖由调用方按内容信号传入
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
