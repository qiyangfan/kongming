import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { AppLayout } from "@/components/app-layout";
import { PermRoute } from "@/components/perm-route";
import AdminHome from "@/pages/AdminHome";
import ChangePassword from "@/pages/ChangePassword";
import ChatPage from "@/pages/ChatPage";
import Login from "@/pages/Login";
import OAuthCallback from "@/pages/OAuthCallback";
import { NotFoundPage } from "@/pages/error-pages";
import "./index.css";

/* 4xx 客户端错误(401 已由刷新链路处理)重试无意义:只对 5xx/网络错误重试两次 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        const status = (error as Error & { status?: number }).status;
        if (status !== undefined && status < 500) return false;
        return failureCount < 2;
      },
    },
  },
});

const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { path: "/auth/:provider/callback", element: <OAuthCallback /> },
  { path: "/change-password", element: <ChangePassword /> },
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      {
        path: "chat",
        element: (
          <PermRoute perm="chat:execute">
            <ChatPage />
          </PermRoute>
        ),
      },
      {
        path: "chat/:sessionId",
        element: (
          <PermRoute perm="chat:execute">
            <ChatPage />
          </PermRoute>
        ),
      },
      {
        path: "admin",
        element: (
          <PermRoute perm="system:user:manage">
            <AdminHome />
          </PermRoute>
        ),
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
