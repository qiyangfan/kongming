// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useTheme } from "@kongming/api-client/theme-store";

import { ThemeToggle } from "@/components/theme-toggle";

describe("管理台主题切换", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
    useTheme.setState({ theme: "light" });
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
    useTheme.setState({ theme: "light" });
  });

  it("点击后翻转主题并写入本地偏好", async () => {
    const user = userEvent.setup();
    const { container } = render(<ThemeToggle />);
    expect(container.querySelector(".lucide-moon")).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "切换为暗色" }));

    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("km-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "切换为亮色" })).toBeInTheDocument();
    expect(container.querySelector(".lucide-sun")).not.toBeNull();
  });
});
