/** 滚动跟随策略(「近底才贴」);判定只用滚动容器的三个度量,不依赖 window/视觉尺寸,可单测。 */

/** 距底 ≤64px 视为"处于底部附近"(radix 隐藏原生滚动条,判定必须挂 viewport)。 */
export const NEAR_BOTTOM_PX = 64;

export function isNearBottom(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
  threshold: number = NEAR_BOTTOM_PX,
): boolean {
  // 内容不足一屏(无滚动余量)时恒视为贴底:新消息同样要跟
  const distance = scrollHeight - clientHeight - scrollTop;
  return distance <= threshold;
}
