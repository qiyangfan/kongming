import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

// jsdom 缺 Radix 弹层/滚动区所需的浏览器能力,按最小空实现补齐(node 环境跳过)
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      // eslint-disable-next-line @typescript-eslint/no-deprecated -- 旧版第三方库仍调用这对旧 API,shim 必须保留
      addListener: () => undefined,
      // eslint-disable-next-line @typescript-eslint/no-deprecated -- 同上,兼容旧 API 消费方
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (typeof window !== "undefined") {
  window.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
  if (typeof Element.prototype.scrollIntoView !== "function") {
    Element.prototype.scrollIntoView = () => undefined;
  }
  if (typeof Element.prototype.hasPointerCapture !== "function") {
    Element.prototype.hasPointerCapture = () => false;
  }
}
