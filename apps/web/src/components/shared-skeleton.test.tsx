// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { Inbox } from "lucide-react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@kongming/ui/empty-state";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { FieldLabel } from "@kongming/ui/field-label";
import { FormRow } from "@kongming/ui/form-row";
import { PageHeader } from "@kongming/ui/page-header";
import { StatCard } from "@kongming/ui/stat-card";
import { Input } from "@kongming/ui/input";

describe("页面骨架标准件", () => {
  it("PageHeader 渲染标题/描述/操作区,缺省项不渲染", () => {
    render(
      <PageHeader
        title="会话"
        description="全部会话"
        actions={<button type="button">新建</button>}
      />,
    );
    expect(screen.getByRole("heading", { name: "会话" })).toBeInTheDocument();
    expect(screen.getByText("全部会话")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建" })).toBeInTheDocument();
  });

  it("PageHeader 无描述与操作区时只留标题", () => {
    const { container } = render(<PageHeader title="仅标题" />);
    expect(screen.getByRole("heading", { name: "仅标题" })).toBeInTheDocument();
    expect(container.querySelector("p")).toBeNull(); // 无描述则不渲染描述节点
    expect(container.querySelector("button")).toBeNull(); // 无操作则不渲染操作区
  });

  it("EmptyState 渲染图标与说明", () => {
    const { container } = render(
      <EmptyState icon={Inbox}>
        <p>暂无数据</p>
      </EmptyState>,
    );
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("ErrorAlert 默认文案与自定义文案均带 alert 语义", () => {
    const { rerender } = render(<ErrorAlert />);
    expect(screen.getByRole("alert")).toHaveTextContent("加载失败,请稍后重试");
    rerender(<ErrorAlert text="权限不足" />);
    expect(screen.getByRole("alert")).toHaveTextContent("权限不足");
  });

  it("StatCard 渲染标签与值", () => {
    render(<StatCard label="总轮次" value={128} />);
    expect(screen.getByText("总轮次")).toBeInTheDocument();
    expect(screen.getByText("128")).toBeInTheDocument();
  });

  it("FormRow 无 help 用普通 Label,有 help 用说明按钮", () => {
    const { rerender } = render(
      <FormRow label="名称" htmlFor="n">
        <Input id="n" />
      </FormRow>,
    );
    expect(screen.getByLabelText("名称")).toBeInTheDocument();
    rerender(
      <FormRow label="名称" htmlFor="n" help="用于展示">
        <Input id="n" />
      </FormRow>,
    );
    expect(screen.getByRole("button", { name: "名称的说明" })).toBeInTheDocument();
  });

  it("FieldLabel 关联控件并渲染说明入口", () => {
    render(
      <>
        <FieldLabel htmlFor="x" help="说明文本">
          字段
        </FieldLabel>
        <Input id="x" />
      </>,
    );
    expect(screen.getByLabelText("字段")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "字段的说明" })).toBeInTheDocument();
  });
});
