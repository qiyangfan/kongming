import * as React from "react";

import { FieldLabel } from "@kongming/ui/field-label";
import { Label } from "@kongming/ui/label";
import { cn } from "@kongming/api-client/utils";

/** 统一表单行布局；传入 help 时显示说明 Tooltip。 */
export function FormRow({
  label,
  htmlFor,
  help,
  children,
  className,
}: {
  label: string;
  htmlFor?: string | undefined;
  help?: string | undefined;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      {help ? (
        <FieldLabel htmlFor={htmlFor} help={help}>
          {label}
        </FieldLabel>
      ) : (
        <Label htmlFor={htmlFor}>{label}</Label>
      )}
      {children}
    </div>
  );
}
