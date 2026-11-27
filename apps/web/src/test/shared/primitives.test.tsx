// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Badge } from "@kongming/ui/badge";
import { Button } from "@kongming/ui/button";
import { Input } from "@kongming/ui/input";
import { Label } from "@kongming/ui/label";
import { Separator } from "@kongming/ui/separator";
import { Skeleton } from "@kongming/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@kongming/ui/table";

describe("基础交互件", () => {
  it("Button 默认变体与尺寸写入 data 属性", () => {
    render(<Button>保存</Button>);
    const btn = screen.getByRole("button", { name: "保存" });
    expect(btn).toHaveAttribute("data-variant", "default");
    expect(btn).toHaveAttribute("data-size", "default");
  });

  it("Button 危险变体与点击回调", async () => {
    const onClick = vi.fn();
    render(
      <Button variant="destructive" size="sm" onClick={onClick}>
        删除
      </Button>,
    );
    const btn = screen.getByRole("button", { name: "删除" });
    expect(btn).toHaveAttribute("data-variant", "destructive");
    await userEvent.click(btn);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("Button 禁用时不触发点击", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        提交
      </Button>,
    );
    await userEvent.click(screen.getByRole("button", { name: "提交" }));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("Button asChild 渲染为子元素", () => {
    render(
      <Button asChild>
        <a href="/portal">返回门户</a>
      </Button>,
    );
    expect(screen.getByRole("link", { name: "返回门户" })).toHaveAttribute("href", "/portal");
  });

  it("Badge 变体含 success/warning", () => {
    render(
      <>
        <Badge variant="success">正常</Badge>
        <Badge variant="warning">注意</Badge>
      </>,
    );
    expect(screen.getByText("正常")).toHaveAttribute("data-variant", "success");
    expect(screen.getByText("注意")).toHaveAttribute("data-variant", "warning");
  });

  it("Input 受控输入与 aria-invalid 透传", async () => {
    const onChange = vi.fn();
    render(<Input aria-label="租户" value="" onChange={onChange} aria-invalid />);
    const input = screen.getByLabelText("租户");
    expect(input).toHaveAttribute("aria-invalid", "true");
    await userEvent.type(input, "a");
    expect(onChange).toHaveBeenCalled();
  });

  it("Label 与控件通过 htmlFor 关联", () => {
    render(
      <>
        <Label htmlFor="name">名称</Label>
        <Input id="name" />
      </>,
    );
    expect(screen.getByLabelText("名称")).toBeInTheDocument();
  });

  it("Table 渲染表头与单元格", () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>列名</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>值</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    expect(screen.getByRole("columnheader", { name: "列名" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "值" })).toBeInTheDocument();
  });

  it("Skeleton 与 Separator 渲染 data-slot", () => {
    const { container } = render(
      <>
        <Skeleton className="h-4" />
        <Separator />
      </>,
    );
    expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull();
    expect(container.querySelector('[data-slot="separator"]')).not.toBeNull();
  });
});
