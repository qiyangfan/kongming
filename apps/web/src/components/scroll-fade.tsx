/* 横向滚动渐隐提示(多屏走查 P1/P2 清偿):容器可横向滚动且未滚到对应缘时,
   在左/右缘渲染背景色渐隐层,暗示"还有内容";无溢出或滚到尽头自动消失,不误导。
   真滚动层解析:自身可滚用自身;否则取子树第一个 .overflow-x-auto 后代
   (shadcn <Table> 自带 table-container 滚动层,渐隐必须挂到真正的滚动层)。
   两种宿主:包裹模式 <ScrollFade>(容器样式经 className 透传,包宽表);
   自滚动宿主(如管理导航)用 useScrollableX + <FadeEdges> 自挂渐隐层。 */

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { cn } from "@/lib/utils";

export function useScrollableX(ref: RefObject<HTMLElement | null>) {
  const [edge, setEdge] = useState({ left: false, right: false });
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    // 真滚动层优先级:第一个 .overflow-x-auto 后代(命中时自身必不溢出),回退自身
    const inner = root.querySelector<HTMLElement>(".overflow-x-auto");
    const layers = inner ? [inner, root] : [root];
    const check = () => {
      for (const el of layers) {
        const max = el.scrollWidth - el.clientWidth;
        if (max > 4) {
          setEdge({ left: el.scrollLeft > 4, right: el.scrollLeft < max - 4 });
          return;
        }
      }
      setEdge({ left: false, right: false });
    };
    check();
    // 观察内容根:数据到达后表格变宽不漏显(容器盒尺寸不变时 RO 不触发)
    const ro = new ResizeObserver(check);
    ro.observe(root);
    if (root.firstElementChild) ro.observe(root.firstElementChild);
    if (inner?.firstElementChild) ro.observe(inner.firstElementChild);
    // scroll 不冒泡,捕获监听以覆盖内层滚动容器
    root.addEventListener("scroll", check, true);
    return () => {
      ro.disconnect();
      root.removeEventListener("scroll", check, true);
    };
  }, [ref]);
  return edge;
}

const fadeCls =
  "pointer-events-none absolute inset-y-0 z-10 w-6 from-background to-transparent";

/* 渐隐层两缘(供自滚动宿主复用);包裹模式由 ScrollFade 内部自挂 */
export function FadeEdges({ left, right }: { left: boolean; right: boolean }) {
  return (
    <>
      {left && <div aria-hidden className={cn(fadeCls, "left-0 bg-gradient-to-r")} />}
      {right && <div aria-hidden className={cn(fadeCls, "right-0 bg-gradient-to-l")} />}
    </>
  );
}

/* 不自供滚动容器:shadcn <Table> 自带 table-container 滚动层,
   此处只做渐隐定位壳,容器样式(含 overflow-x-auto)经 className 透传 */
export function ScrollFade({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const edge = useScrollableX(ref);
  return (
    <div ref={ref} className={cn("relative", className)}>
      {children}
      <FadeEdges {...edge} />
    </div>
  );
}
