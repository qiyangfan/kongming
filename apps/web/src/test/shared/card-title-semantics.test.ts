import type { ReactElement } from "react";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CardTitle } from "@kongming/ui/card";

/* 用服务端渲染检查 CardTitle 的默认标题标签、自定义标签和 className 透传。 */

const VISUAL_CLASS = "text-lg font-semibold leading-none tracking-tight";

const render = (element: ReactElement) => renderToStaticMarkup(element);

describe("CardTitle 标题语义", () => {
  it("默认输出 h3 且携带视觉配方", () => {
    const html = render(createElement(CardTitle, null, "孔明 Kongming"));
    expect(html).toBe(`<h3 class="${VISUAL_CLASS}">孔明 Kongming</h3>`);
  });

  it('as="h2" 输出 h2(认证入口页的页面级卡题)', () => {
    const html = render(createElement(CardTitle, { as: "h2" }, "修改密码"));
    expect(html).toBe(`<h2 class="${VISUAL_CLASS}">修改密码</h2>`);
  });

  it('as="h4" 输出 h4(区块标题之下的卡片)', () => {
    const html = render(createElement(CardTitle, { as: "h4" }, "区块内卡片"));
    expect(html).toBe(`<h4 class="${VISUAL_CLASS}">区块内卡片</h4>`);
  });

  it("视觉配方打底不丢,调用方附加类正常合并", () => {
    /* 配方类与非冲突附加类合并后逐字节保留;同组冲突类(如 text-xl 对 text-lg) 按 cn/tailwind-merge 既有语义由调用方胜出,属组件外通用约定,不在本守卫钉。 */
    const html = render(createElement(CardTitle, { as: "h2", className: "mb-2" }, "登录"));
    expect(html).toBe(`<h2 class="${VISUAL_CLASS} mb-2">登录</h2>`);
  });

  it("ref 与其余 props 透传不受多态标签影响", () => {
    const html = render(
      createElement(CardTitle, { as: "h3", id: "card-title", title: "卡片标题" }, "标题"),
    );
    expect(html).toBe(`<h3 class="${VISUAL_CLASS}" id="card-title" title="卡片标题">标题</h3>`);
  });
});
