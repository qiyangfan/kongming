// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SqlArtifactCard } from "@/components/artifact-blocks";
import { StreamCard } from "@kongming/ui/stream-card";
import { rerunArtifact } from "@kongming/api-client/chat-api";

vi.mock("@kongming/api-client/chat-api", () => ({ rerunArtifact: vi.fn() }));

const mockRerun = vi.mocked(rerunArtifact);
const SQL = "SELECT count(*) FROM film";

function renderCard(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SqlArtifactCard messageId="m-1" sql={SQL} />
    </QueryClientProvider>,
  );
}

async function expand(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole("button", { name: /SQL/ }));
}

async function enterEdit(user: ReturnType<typeof userEvent.setup>): Promise<HTMLTextAreaElement> {
  await user.click(screen.getByRole("button", { name: "编辑" }));
  const textarea = screen.getByRole("textbox", { name: "编辑 SQL" });
  await waitFor(() => expect(textarea).toHaveFocus());
  return textarea as HTMLTextAreaElement;
}

describe("SqlArtifactCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("展开默认只读:无编辑控件,可复制", async () => {
    const user = userEvent.setup();
    renderCard();
    await expand(user);
    expect(screen.queryByRole("textbox", { name: "编辑 SQL" })).toBeNull();
    expect(document.querySelector("code.language-sql")?.textContent).toContain("SELECT count");
    await user.click(screen.getByRole("button", { name: "复制 SQL" }));
    await expect(navigator.clipboard.readText()).resolves.toBe(SQL);
  });

  it("进入编辑后聚焦文本框", async () => {
    const user = userEvent.setup();
    renderCard();
    await expand(user);
    const textarea = await enterEdit(user);
    expect(textarea.value).toBe(SQL);
  });

  it("重新执行成功后回到只读态且焦点回编辑按钮", async () => {
    mockRerun.mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderCard();
    await expand(user);
    const textarea = await enterEdit(user);
    await user.clear(textarea);
    await user.type(textarea, "SELECT 2");
    await user.click(screen.getByRole("button", { name: "重新执行" }));
    expect(mockRerun).toHaveBeenCalledWith("m-1", "SELECT 2");
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "编辑 SQL" })).toBeNull());
    await waitFor(() => expect(screen.getByRole("button", { name: "编辑" })).toHaveFocus());
  });

  it("重新执行失败停留编辑态并显示错误", async () => {
    mockRerun.mockRejectedValue(new Error("再执行未成"));
    const user = userEvent.setup();
    renderCard();
    await expand(user);
    await enterEdit(user);
    await user.click(screen.getByRole("button", { name: "重新执行" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("再执行未成");
    expect(screen.getByRole("textbox", { name: "编辑 SQL" })).toBeTruthy();
  });

  it("还原回到只读并丢弃草稿", async () => {
    const user = userEvent.setup();
    renderCard();
    await expand(user);
    const textarea = await enterEdit(user);
    await user.clear(textarea);
    await user.type(textarea, "SELECT 99");
    await user.click(screen.getByRole("button", { name: "还原" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "编辑 SQL" })).toBeNull());
    await user.click(screen.getByRole("button", { name: "编辑" }));
    expect(screen.getByRole<HTMLTextAreaElement>("textbox", { name: "编辑 SQL" }).value).toBe(SQL);
  });

  it("收起再展开回只读态", async () => {
    const user = userEvent.setup();
    renderCard();
    await expand(user);
    await enterEdit(user);
    await user.click(screen.getByRole("button", { name: /SQL/ }));
    await expand(user);
    expect(screen.queryByRole("textbox", { name: "编辑 SQL" })).toBeNull();
  });
});

describe("StreamCard SQL", () => {
  it("流式 SQL 走 Markdown SQL 代码块,不是裸文本", () => {
    render(<StreamCard stages={[]} sql="SELECT count(*) FROM entity" summary="" />);
    expect(document.querySelector("code.language-sql")?.textContent).toContain("SELECT count");
  });
});
