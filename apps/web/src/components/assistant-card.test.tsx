// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AssistantCard } from "@/components/assistant-card";
import type { SessionMessageOut } from "@/lib/api";

const SQL = "SELECT count(*) FROM film";

function renderMessage(msg: SessionMessageOut): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AssistantCard msg={msg} />
    </QueryClientProvider>,
  );
}

describe("AssistantCard SQL 回退", () => {
  it("无 sql 产物时仍走只读高亮+编辑卡,不渲染裸 pre", async () => {
    renderMessage({
      id: "m-1",
      role: "assistant",
      status: "OK",
      sql: SQL,
      attachments: [],
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /SQL/ }));
    expect(document.querySelector("code.language-sql")?.textContent).toContain("SELECT count");
    expect(screen.getByRole("button", { name: "编辑" })).toBeTruthy();
  });
});
