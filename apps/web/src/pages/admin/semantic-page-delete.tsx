/* 语义层删除确认与删除失败回显(失败时 id 清空,靠回显框单独呈现原因)。 */

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { SemanticDeleteTarget } from "./semantic-page-data";

export function SemanticDeleteDialogs({
  target,
  pending,
  onChange,
  onConfirm,
}: {
  target: SemanticDeleteTarget | null;
  pending: boolean;
  onChange: (t: SemanticDeleteTarget | null) => void;
  onConfirm: (t: SemanticDeleteTarget) => void;
}) {
  const kindText = target?.kind === "rel" ? "表关系" : "指标";
  return (
    <>
      {/* 删除受控确认,失败行内回显 */}
      <AlertDialog
        open={target !== null && target.id !== ""}
        onOpenChange={(open) => {
          if (!open) onChange(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              删除{kindText}「{target?.name}」?
            </AlertDialogTitle>
            <AlertDialogDescription>
              删除后,AI 生成回答时不再参考该{target?.kind === "rel" ? "表关联" : "指标口径"}。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {target?.error && (
            <p className="text-sm text-destructive" role="alert">
              {target.error}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={pending}
              onClick={() => {
                if (!target || !target.id) return;
                onConfirm(target);
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 删除失败回显框:错误时 id 已清空,靠 open 条件保持可见 */}
      <AlertDialog
        open={target !== null && target.id === "" && !!target.error}
        onOpenChange={(open) => {
          if (!open) onChange(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除失败</AlertDialogTitle>
            <AlertDialogDescription>{target?.error}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>知道了</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
