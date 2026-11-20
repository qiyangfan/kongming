import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, type ReactNode, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { AppLayout } from "@/components/app-layout";
import { PermRoute } from "@/components/perm-route";
import { Skeleton } from "@/components/ui/skeleton";
import ChangePassword from "@/pages/ChangePassword";
import Login from "@/pages/Login";
import OAuthCallback from "@/pages/OAuthCallback";
import { NotFoundPage } from "@/components/error-pages";
import "./index.css";

/* 首屏外路由懒加载；登录页与公开 Bot 嵌入页保持静态导入。 */

const MemoryPage = lazy(() => import("@/pages/MemoryPage"));
const ReportsPage = lazy(() => import("@/pages/ReportsPage"));
const ReportDetailPage = lazy(() => import("@/pages/ReportDetailPage"));
const TasksPage = lazy(() => import("@/pages/TasksPage"));
const TaskDetailPage = lazy(() => import("@/pages/TaskDetailPage"));
const PortalPage = lazy(() => import("@/pages/PortalPage"));
const PayPage = lazy(() => import("@/pages/PayPage"));
const StorePage = lazy(() => import("@/pages/StorePage"));
const ChatPage = lazy(() => import("@/pages/ChatPage"));
const BotPublicPage = lazy(() => import("@/pages/BotPublicPage"));
const AdminLayout = lazy(() => import("@/pages/admin/AdminLayout"));
const AdminIndexPage = lazy(() => import("@/pages/admin/AdminIndexPage"));
const DatasourcesPage = lazy(() => import("@/pages/admin/DatasourcesPage"));
const DeptsPage = lazy(() => import("@/pages/admin/DeptsPage"));
const RolesPage = lazy(() => import("@/pages/admin/RolesPage"));
const UsersPage = lazy(() => import("@/pages/admin/UsersPage"));
const RagPage = lazy(() => import("@/pages/admin/RagPage"));
const AuditPage = lazy(() => import("@/pages/admin/AuditPage"));
const ToolsPage = lazy(() => import("@/pages/admin/ToolsPage"));
const FeedbackPage = lazy(() => import("@/pages/admin/FeedbackPage"));
const BillingPage = lazy(() => import("@/pages/admin/BillingPage"));
const GuardrailsPage = lazy(() => import("@/pages/admin/GuardrailsPage"));
const SemanticPage = lazy(() => import("@/pages/admin/SemanticPage"));
const BotsPage = lazy(() => import("@/pages/admin/BotsPage"));
const McpPage = lazy(() => import("@/pages/admin/McpPage"));
const ConnectorsPage = lazy(() => import("@/pages/admin/ConnectorsPage"));
const WorkflowsPage = lazy(() => import("@/pages/admin/WorkflowsPage"));
const TriggersPage = lazy(() => import("@/pages/admin/TriggersPage"));
const TeamsPage = lazy(() => import("@/pages/admin/TeamsPage"));
const UsagePage = lazy(() => import("@/pages/admin/UsagePage"));
const QualityPage = lazy(() => import("@/pages/admin/QualityPage"));

function RouteSuspenseFallback() {
  return (
    <div className="p-4">
      <Skeleton className="h-96 w-full" />
    </div>
  );
}

/** 路由级 Suspense 统一包装,骨架固定复用 RouteSuspenseFallback。 */
function el(node: ReactNode): ReactNode {
  return <Suspense fallback={<RouteSuspenseFallback />}>{node}</Suspense>;
}

/* 不重试 4xx；401 已由令牌刷新处理，网络错误和 5xx 最多重试两次。 */
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
    element: el(<AppLayout />),
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      {
        path: "chat",
        element: el(
          <PermRoute perm="chat:execute">
            <ChatPage />
          </PermRoute>,
        ),
      },
      {
        path: "chat/:sessionId",
        element: el(
          <PermRoute perm="chat:execute">
            <ChatPage />
          </PermRoute>,
        ),
      },
      {
        path: "portal",
        element: el(
          <PermRoute perm="developer:portal">
            <PortalPage />
          </PermRoute>,
        ),
      },
      {
        path: "portal/pay/:paymentId",
        element: el(
          <PermRoute perm="developer:portal">
            <PayPage />
          </PermRoute>,
        ),
      },
      {
        path: "store",
        element: el(
          <PermRoute perm="bot:run">
            <StorePage />
          </PermRoute>,
        ),
      },
      {
        path: "tasks",
        element: el(
          <PermRoute perm="task:read">
            <TasksPage />
          </PermRoute>,
        ),
      },
      {
        path: "tasks/:taskId",
        element: el(
          <PermRoute perm="task:read">
            <TaskDetailPage />
          </PermRoute>,
        ),
      },
      {
        path: "reports",
        element: el(
          <PermRoute perm="report:read">
            <ReportsPage />
          </PermRoute>,
        ),
      },
      {
        path: "reports/:reportId",
        element: el(
          <PermRoute perm="report:read">
            <ReportDetailPage />
          </PermRoute>,
        ),
      },
      {
        path: "memory",
        element: el(
          <PermRoute perm="chat:execute">
            <MemoryPage />
          </PermRoute>,
        ),
      },
      {
        path: "admin",
        element: el(<AdminLayout />),
        children: [
          { index: true, element: el(<AdminIndexPage />) },
          {
            path: "users",
            element: el(
              <PermRoute perm="system:user:manage">
                <UsersPage />
              </PermRoute>,
            ),
          },
          {
            path: "roles",
            element: el(
              <PermRoute perm="system:role:manage">
                <RolesPage />
              </PermRoute>,
            ),
          },
          {
            path: "depts",
            element: el(
              <PermRoute perm="system:user:manage">
                <DeptsPage />
              </PermRoute>,
            ),
          },
          {
            path: "datasources",
            element: el(
              <PermRoute perm="system:datasource:manage">
                <DatasourcesPage />
              </PermRoute>,
            ),
          },
          {
            path: "rag",
            element: el(
              <PermRoute perm="system:rag:manage">
                <RagPage />
              </PermRoute>,
            ),
          },
          {
            path: "feedback",
            element: el(
              <PermRoute perm="system:feedback:view">
                <FeedbackPage />
              </PermRoute>,
            ),
          },
          {
            path: "audit",
            element: el(
              <PermRoute perm="system:audit:view">
                <AuditPage />
              </PermRoute>,
            ),
          },
          {
            path: "tools",
            element: el(
              <PermRoute perm="system:tool:manage">
                <ToolsPage />
              </PermRoute>,
            ),
          },
          {
            path: "guardrails",
            element: el(
              <PermRoute perm="system:guardrail:manage">
                <GuardrailsPage />
              </PermRoute>,
            ),
          },
          {
            path: "semantic",
            element: el(
              <PermRoute perm="system:semantic:manage">
                <SemanticPage />
              </PermRoute>,
            ),
          },
          {
            path: "mcp",
            element: el(
              <PermRoute perm="system:mcp:manage">
                <McpPage />
              </PermRoute>,
            ),
          },
          {
            path: "connectors",
            element: el(
              <PermRoute perm="connector:manage">
                <ConnectorsPage />
              </PermRoute>,
            ),
          },
          {
            path: "workflow",
            element: el(
              <PermRoute perm="system:workflow:manage">
                <WorkflowsPage />
              </PermRoute>,
            ),
          },
          {
            path: "triggers",
            element: el(
              <PermRoute perm="system:trigger:manage">
                <TriggersPage />
              </PermRoute>,
            ),
          },
          {
            path: "billing",
            element: el(
              <PermRoute perm="system:billing:manage">
                <BillingPage />
              </PermRoute>,
            ),
          },
          {
            path: "teams",
            element: el(
              <PermRoute perm="system:team:manage">
                <TeamsPage />
              </PermRoute>,
            ),
          },
          {
            path: "bots",
            element: el(
              <PermRoute perm="chat:execute">
                <BotsPage />
              </PermRoute>,
            ),
          },
          {
            path: "usage",
            element: el(
              <PermRoute perm="system:llm-usage:view">
                <UsagePage />
              </PermRoute>,
            ),
          },
          {
            path: "quality",
            element: el(
              <PermRoute perm="system:chat-quality:view">
                <QualityPage />
              </PermRoute>,
            ),
          },
        ],
      },
      { path: "*", element: el(<NotFoundPage />) },
    ],
  },
  {
    // 免登录公开对话页;必须位于认证壳外,否则 AppLayout 会把匿名访客重定向到 /login。
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
