// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@kongming/ui/alert-dialog";
import { Checkbox } from "@kongming/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@kongming/ui/collapsible";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@kongming/ui/dialog";
import { Sheet, SheetContent, SheetTitle } from "@kongming/ui/sheet";
import { Switch } from "@kongming/ui/switch";
import { Toggle } from "@kongming/ui/toggle";

describe("弹层与开关类控件", () => {
  it("Dialog 渲染标题/描述,关闭按钮回传 onOpenChange(false)", async () => {
    const onOpenChange = vi.fn();
    render(
      <Dialog open onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogTitle>重命名会话</DialogTitle>
          <DialogDescription>输入新标题</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "重命名会话" })).toBeInTheDocument();
    expect(screen.getByText("输入新标题")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("Dialog showCloseButton=false 不渲染关闭入口", () => {
    render(
      <Dialog open>
        <DialogContent showCloseButton={false}>
          <DialogTitle>无关闭钮</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  });

  it("AlertDialog 操作与取消按钮均回传关闭", async () => {
    const onOpenChange = vi.fn();
    render(
      <AlertDialog open onOpenChange={onOpenChange}>
        <AlertDialogContent>
          <AlertDialogTitle>确认删除</AlertDialogTitle>
          <AlertDialogDescription>删除后不可恢复</AlertDialogDescription>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                onOpenChange(false);
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>,
    );
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    const afterAction = onOpenChange.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onOpenChange.mock.calls).toHaveLength(afterAction + 1);
  });

  it("Sheet 渲染侧边容器与标题", () => {
    render(
      <Sheet open>
        <SheetContent>
          <SheetTitle>会话抽屉</SheetTitle>
        </SheetContent>
      </Sheet>,
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "会话抽屉" })).toBeInTheDocument();
  });

  it("Collapsible 展开前后内容可见性切换", async () => {
    render(
      <Collapsible>
        <CollapsibleTrigger>展开</CollapsibleTrigger>
        <CollapsibleContent>折叠内容</CollapsibleContent>
      </Collapsible>,
    );
    expect(screen.queryByText("折叠内容")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "展开" }));
    expect(screen.getByText("折叠内容")).toBeInTheDocument();
  });

  it("Checkbox 点击切换选中态", async () => {
    render(<Checkbox aria-label="启用" />);
    const box = screen.getByRole("checkbox", { name: "启用" });
    expect(box).toHaveAttribute("data-state", "unchecked");
    await userEvent.click(box);
    expect(box).toHaveAttribute("data-state", "checked");
  });

  it("Switch 点击切换并支持 sm 尺寸", async () => {
    render(<Switch aria-label="通知" size="sm" />);
    const sw = screen.getByRole("switch", { name: "通知" });
    expect(sw).toHaveAttribute("data-size", "sm");
    await userEvent.click(sw);
    expect(sw).toHaveAttribute("data-state", "checked");
  });

  it("Toggle 点击切换按下态", async () => {
    render(<Toggle aria-label="加粗">B</Toggle>);
    const t = screen.getByRole("button", { name: "加粗" });
    expect(t).toHaveAttribute("data-state", "off");
    await userEvent.click(t);
    expect(t).toHaveAttribute("data-state", "on");
  });
});
