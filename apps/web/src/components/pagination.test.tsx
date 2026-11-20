// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Pagination } from "@/components/pagination";

describe("Pagination", () => {
  it("展示总数与页码,首页禁用上一页", async () => {
    const onPageChange = vi.fn();
    render(<Pagination page={1} pageCount={3} total={42} onPageChange={onPageChange} />);
    expect(screen.getByText("共 42 条")).toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    const prev = screen.getByRole("button", { name: "上一页" });
    expect(prev).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("末页禁用下一页,中间页可双向翻", async () => {
    const onPageChange = vi.fn();
    render(<Pagination page={3} pageCount={3} onPageChange={onPageChange} />);
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it("无 total 时不显示总数文案", () => {
    render(<Pagination page={2} pageCount={5} onPageChange={vi.fn()} />);
    expect(screen.queryByText(/共 .* 条/)).toBeNull();
    expect(screen.getByText("2 / 5")).toBeInTheDocument();
  });
});
