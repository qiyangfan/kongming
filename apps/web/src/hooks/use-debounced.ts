import { useEffect, useState } from "react";

/** 输入型筛选防抖(共享单源,038⑦ 清偿:UsagePage 局部实现抽公共 hook)。
 * @param value 原始输入值
 * @param delayMs 防抖毫秒(缺省 300)
 */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}
