/* StatusSwitch(012):管理表格行内启停开关,用户页/数据源页共用。
   受控 checked=服务器真值(react-query 缓存计算),请求飞行期禁用防抖动;
   失败时缓存未变→开关自然回弹,错误行内 role=alert 暴露(008「失败静默」教训固化)。 */

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
