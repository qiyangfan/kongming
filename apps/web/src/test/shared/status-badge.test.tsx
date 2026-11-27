// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge, type StatusMeta } from "@kongming/ui/status-badge";

const META: StatusMeta = {
  PASS: { label: "通过", variant: "success" },
  ERROR: { label: "失败", variant: "destructive" },
};

describe("StatusBadge", () => {
  it("命中映射时用映射标签与变体", () => {
    render(<StatusBadge meta={META} value="PASS" />);
    const badge = screen.getByText("通过");
    expect(badge).toHaveAttribute("data-variant", "success");
  });

  it("未命中时回退 fallbackLabel", () => {
    render(<StatusBadge meta={META} value="UNKNOWN" fallbackLabel="未知" />);
    expect(screen.getByText("未知")).toHaveAttribute("data-variant", "outline");
  });

  it("未命中且无 fallback 时显示原值", () => {
    render(<StatusBadge meta={META} value="PENDING" />);
    expect(screen.getByText("PENDING")).toBeInTheDocument();
  });

  it("空值显示占位并采用指定 fallbackVariant", () => {
    render(<StatusBadge meta={META} value={null} fallbackVariant="ghost" />);
    expect(screen.getByText("—")).toHaveAttribute("data-variant", "ghost");
  });
});
