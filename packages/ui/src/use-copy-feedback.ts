import { useCallback, useEffect, useRef, useState } from "react";

/** 复制成功状态的短时反馈;重复复制重置计时,卸载清理定时器。 */
export function useCopyFeedback(timeoutMs = 1500) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    },
    [],
  );

  const markCopied = useCallback(() => {
    setCopied(true);
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), timeoutMs);
  }, [timeoutMs]);

  return { copied, markCopied };
}
