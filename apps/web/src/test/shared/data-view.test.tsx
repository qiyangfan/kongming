// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataView, type DataViewColumn } from "@kongming/ui/data-view";

interface Row {
  id: string;
  name: string;
  status: string;
}

const COLUMNS: DataViewColumn<Row>[] = [
  { key: "name", header: "名称", cardPriority: 1 },
  { key: "status", header: "状态", cardPriority: 2 },
];

const ROWS: Row[] = [
  { id: "1", name: "甲库", status: "启用" },
  { id: "2", name: "乙库", status: "停用" },
];

describe("DataView", () => {
  it("表格与卡片两形态共用列定义(默认取行字段)", () => {
    render(<DataView columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);
    expect(screen.getAllByText("甲库")).not.toHaveLength(0);
    expect(screen.getAllByText("停用")).not.toHaveLength(0);
  });

  it("render 自定义单元格优先于默认取值", () => {
    const columns: DataViewColumn<Row>[] = [
      { key: "name", header: "名称", render: (r) => <strong>{r.name}!</strong> },
    ];
    render(<DataView columns={columns} rows={ROWS} rowKey={(r) => r.id} />);
    expect(screen.getAllByText("甲库!")).not.toHaveLength(0);
  });

  it("行操作按 isVisible 过滤、isDisabled 降级并触发 onSelect", async () => {
    const onDelete = vi.fn();
    const actions = [
      { label: "删除", onSelect: onDelete, isVisible: (r: Row) => r.status === "停用" },
      { label: "查看", onSelect: vi.fn(), isDisabled: () => true, disabledReason: "无权限" },
    ];
    render(<DataView columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} actions={actions} />);
    // 停用行:删除按钮可见;启用行:不可见
    const deleteButtons = screen.getAllByRole("button", { name: "删除" });
    expect(deleteButtons).toHaveLength(1);
    const firstDelete = deleteButtons[0];
    if (!firstDelete) throw new Error("未找到删除按钮");
    await userEvent.click(firstDelete);
    expect(onDelete).toHaveBeenCalledWith(ROWS[1]);

    const viewButtons = screen.getAllByRole("button", { name: "查看" });
    expect(viewButtons[0]).toBeDisabled();
    expect(viewButtons[0]).toHaveAttribute("title", "无权限");
  });

  it("加载态与空态使用各自文案", () => {
    render(
      <DataView
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.id}
        isLoading
        loadingText="正在读取"
        isEmpty
        emptyText="没有记录"
      />,
    );
    expect(screen.getAllByText("正在读取").length).toBeGreaterThan(0);
    // 加载中不叠加空态
    expect(screen.queryByText("没有记录")).toBeNull();
  });

  it("空态在非加载时渲染", () => {
    render(
      <DataView columns={COLUMNS} rows={[]} rowKey={(r) => r.id} isEmpty emptyText="没有记录" />,
    );
    expect(screen.getAllByText("没有记录").length).toBeGreaterThan(0);
  });
});
