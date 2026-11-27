// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";

const { acceptTokens } = vi.hoisted(() => ({
  acceptTokens: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@kongming/api-client/auth-store", () => ({
  useAuth: (selector: (state: { acceptTokens: typeof acceptTokens }) => unknown) =>
    selector({ acceptTokens }),
}));

import OAuthCallback from "@/pages/OAuthCallback";

describe("管理台 OAuth 回调", () => {
  it("StrictMode 双挂载只提交一次回调", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ access_token: "a", refresh_token: "r" }),
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/auth/keycloak/callback?code=c&state=s"]}>
          <Routes>
            <Route path="/auth/keycloak/callback" element={<OAuthCallback />} />
            <Route path="/" element={<div>home</div>} />
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    );
    await waitFor(() => expect(acceptTokens).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("home")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
