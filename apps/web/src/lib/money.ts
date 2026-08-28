// 计费金额存储单位=整数厘(1 元=1000 厘),与 api billing models/spec 同口径;
// 展示层一律经此转元,禁止直接把厘数值原样示人。
// 前置:li 为非负整数(DB 金额列非负契约;负数输入不在防御范围,畸形输出即暴露异常源)。
// 整数运算拼小数,不走浮点除法,避免 0.1 类二进制误差进展示。
export function li2yuan(li: number): string {
  const yuan = Math.trunc(li / 1000);
  const rem = li % 1000;
  return `${yuan.toLocaleString()}.${String(rem).padStart(3, "0")}`;
}

// 录入回填用:厘→元的裸数字串(如 2→"0.002"),不带千分位、不补零。
export function li2yuanInput(li: number): string {
  return (li / 1000).toString();
}
