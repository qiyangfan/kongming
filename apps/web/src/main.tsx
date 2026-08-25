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
import ToolsPage from "@/pages/admin/ToolsPage";
import MemoryPage from "@/pages/MemoryPage";
import PortalPage from "@/pages/PortalPage";
import PayPage from "@/pages/PayPage";
import StorePage from "@/pages/StorePage";
import BillingPage from "@/pages/admin/BillingPage";
import BotPublicPage from "@/pages/BotPublicPage";
import GuardrailsPage from "@/pages/admin/GuardrailsPage";
import SemanticPage from "@/pages/admin/SemanticPage";
import BotsPage from "@/pages/admin/BotsPage";
import McpPage from "@/pages/admin/McpPage";
import WorkflowsPage from "@/pages/admin/WorkflowsPage";
import TriggersPage from "@/pages/admin/TriggersPage";
import TeamsPage from "@/pages/admin/TeamsPage";
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
        path: "portal",
        element: (
          <PermRoute perm="developer:portal">
            <PortalPage />
          </PermRoute>
        ),
      },
      {
        path: "portal/pay/:paymentId",
        element: (
          <PermRoute perm="developer:portal">
            <PayPage />
          </PermRoute>
        ),
      },
      {
        path: "store",
        element: (
          <PermRoute perm="bot:run">
            <StorePage />
          </PermRoute>
        ),
      },
      {
        path: "memory",
        element: (
          <PermRoute perm="chat:execute">
            <MemoryPage />
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
          {
            path: "tools",
            element: (
              <PermRoute perm="system:tool:manage">
                <ToolsPage />
              </PermRoute>
            ),
          },
          {
            path: "guardrails",
            element: (
              <PermRoute perm="system:guardrail:manage">
                <GuardrailsPage />
              </PermRoute>
            ),
          },
          {
            path: "semantic",
            element: (
              <PermRoute perm="system:semantic:manage">
                <SemanticPage />
              </PermRoute>
            ),
          },
          {
            path: "mcp",
            element: (
              <PermRoute perm="system:mcp:manage">
                <McpPage />
              </PermRoute>
            ),
          },
          {
            path: "workflow",
            element: (
              <PermRoute perm="system:workflow:manage">
                <WorkflowsPage />
              </PermRoute>
            ),
          },
          {
            path: "triggers",
            element: (
              <PermRoute perm="system:trigger:manage">
                <TriggersPage />
              </PermRoute>
            ),
          },
          {
            path: "billing",
            element: (
              <PermRoute perm="system:billing:manage">
                <BillingPage />
              </PermRoute>
            ),
          },
          {
            path: "teams",
            element: (
              <PermRoute perm="system:team:manage">
                <TeamsPage />
              </PermRoute>
            ),
          },
          {
            path: "bots",
            element: (
              <PermRoute perm="chat:execute">
                <BotsPage />
              </PermRoute>
            ),
          },
        ],
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
  {
    // 025 发布渠道:免登录公开对话页——必须在认证壳外(AppLayout 的
    // Protected 会把匿名访客踢去 /login,嵌入渠道核心场景不可用——审查循环 S1)
    path: "/bot/:botKey",
    element: <BotPublicPage />,
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
