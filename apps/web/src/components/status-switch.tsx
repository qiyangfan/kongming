/* 请求期间禁用开关；失败时保留服务端状态并显示行内错误。 */

import { Switch } from "@/components/ui/switch";

interface StatusSwitchProps {
  active: boolean;
  pending?: boolean;
  error?: string | null;
  onToggle: () => void;
  ariaLabel: string;
}

export function StatusSwitch({
  active,
  pending = false,
  error,
  onToggle,
  ariaLabel,
}: StatusSwitchProps) {
  // id 不允许 ASCII 空白,标签含空格时折为连字符(CJK 字符合法)
  const errorId = `${ariaLabel.replace(/\s+/g, "-")}-error`;
  return (
    <div className="flex flex-col gap-1">
      <Switch
        checked={active}
        disabled={pending}
        onCheckedChange={onToggle}
        aria-label={ariaLabel}
        aria-describedby={error ? errorId : undefined}
      />
      {error ? (
        <p role="alert" id={errorId} className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
