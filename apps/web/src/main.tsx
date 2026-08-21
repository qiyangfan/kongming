import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { AppLayout } from "@/components/app-layout";
import { PermRoute } from "@/components/perm-route";
import AdminLayout from "@/pages/admin/AdminLayout";
import DatasourcesPage from "@/pages/admin/DatasourcesPage";
import DeptsPage from "@/pages/admin/DeptsPage";
import RolesPage from "@/pages/admin/RolesPage";
import UsersPage from "@/pages/admin/UsersPage";
import RagPage from "@/pages/admin/RagPage";
import FeedbackPage from "@/pages/admin/FeedbackPage";
import AuditPage from "@/pages/admin/AuditPage";
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
        element: <AdminLayout />,
        children: [
          { index: true, element: <Navigate to="/admin/users" replace /> },
          {
            path: "users",
            element: (
              <PermRoute perm="system:user:manage">
                <UsersPage />
              </PermRoute>
            ),
          },
          {
            path: "roles",
            element: (
              <PermRoute perm="system:role:manage">
                <RolesPage />
              </PermRoute>
            ),
          },
          {
            path: "depts",
            element: (
              <PermRoute perm="system:user:manage">
                <DeptsPage />
              </PermRoute>
            ),
          },
          {
            path: "datasources",
            element: (
              <PermRoute perm="system:datasource:manage">
                <DatasourcesPage />
              </PermRoute>
            ),
          },
          {
            path: "rag",
            element: (
              <PermRoute perm="system:rag:manage">
                <RagPage />
              </PermRoute>
            ),
          },
          {
            path: "feedback",
            element: (
              <PermRoute perm="system:feedback:view">
                <FeedbackPage />
              </PermRoute>
            ),
          },
          {
            path: "audit",
            element: (
              <PermRoute perm="system:audit:view">
                <AuditPage />
              </PermRoute>
            ),
          },
        ],
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
