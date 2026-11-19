import { describe, expect, it } from "vitest";

import { isNearBottom, NEAR_BOTTOM_PX } from "./scroll-follow";

/* 近底跟随锚:阈值单一常量;内容不足一屏恒贴底(新消息要跟)。 度量 = scrollTop/scrollHeight/clientHeight 三元组,不依赖 window。 */
describe("isNearBottom", () => {
  it("恰好贴底(distance=0)为近底", () => {
    expect(isNearBottom({ scrollTop: 500, scrollHeight: 1000, clientHeight: 500 })).toBe(true);
  });

  it("边界:distance ≤ 阈值(64px)即近底,差 1px 即非", () => {
    expect(isNearBottom({ scrollTop: 436, scrollHeight: 1000, clientHeight: 500 })).toBe(true); // distance=64
    expect(isNearBottom({ scrollTop: 435, scrollHeight: 1000, clientHeight: 500 })).toBe(false); // distance=65
    expect(NEAR_BOTTOM_PX).toBe(64);
  });

  it("内容不足一屏(无滚动余量)恒近底", () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 400, clientHeight: 500 })).toBe(true);
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 500, clientHeight: 500 })).toBe(true);
  });

  it("用户上滚远离底部即非近底(长结果表查阅场景)", () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 10000, clientHeight: 500 })).toBe(false); // 内容顶部
    expect(isNearBottom({ scrollTop: 9400, scrollHeight: 10000, clientHeight: 500 })).toBe(false); // distance=
  });

  it("自定义阈值生效(阈值单一常量可注入,禁散落魔法数)", () => {
    expect(isNearBottom({ scrollTop: 9400, scrollHeight: 10000, clientHeight: 500 }, 200)).toBe(
      true,
    );
    expect(
      isNearBottom({ scrollTop: 0, scrollHeight: 1000, clientHeight: 500 }, NEAR_BOTTOM_PX),
    ).toBe(false); // distance=500
  });
});
