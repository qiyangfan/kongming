/* 变更助手归属团队的对话框;仅目标团队负责人可执行。 */

import { Loader2 } from "lucide-react";

import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { FormRow } from "@kongming/ui/form-row";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import type { TeamItem } from "@kongming/api-client/team-api";

export function AttachDialog({
  open,
  team,
  onTeamChange,
  teams,
  error,
  pending,
  onSubmit,
  onClose,
}: {
  open: boolean;
  team: string;
  onTeamChange: (team: string) => void;
  teams: TeamItem[];
  error: string;
  pending: boolean;
  onSubmit: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (pending) return;
        if (!o) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>变更归属</DialogTitle>
          <DialogDescription>
            将助手归属到团队后,团队成员按角色共享(观察者可见可运行/
            成员可编辑/负责人全权);仅目标团队负责人可执行。
          </DialogDescription>
        </DialogHeader>
        <FormRow label="归属团队">
          <Select value={team} onValueChange={onTeamChange}>
            <SelectTrigger aria-label="归属团队">
              <SelectValue placeholder="选择团队" />
            </SelectTrigger>
            <SelectContent>
              {teams.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </FormRow>
        <DialogFooter>
          <Button onClick={onSubmit} disabled={pending || !team}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            确认变更
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
