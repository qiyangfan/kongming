/* 任务操作按钮:普通操作与需要二次确认的破坏性操作(宪法 Article 2)。 */

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

export function OperationButton(props: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={props.onClick} disabled={props.disabled}>
      {props.label}
    </Button>
  );
}

/** 破坏性操作按钮(宪法 Article 2:不可逆终态迁移必须 AlertDialog 确认)。 */
export function ConfirmOperationButton(props: {
  label: string;
  title: string;
  description: string;
  confirmLabel: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="ghost" size="sm" className="text-destructive" disabled={props.disabled}>
          {props.label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{props.title}</AlertDialogTitle>
          <AlertDialogDescription>{props.description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>再想想</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={props.onClick}>
            {props.confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
