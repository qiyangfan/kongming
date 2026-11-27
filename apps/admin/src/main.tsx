import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, type ReactNode, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { configureAuthBase } from "@kongming/api-client/api";
import { PermRoute } from "@kongming/ui/perm-route";
import { Protected } from "@kongming/ui/protected";
import { NotFoundPage } from "@kongming/ui/error-pages";
import { Skeleton } from "@kongming/ui/skeleton";

import Login from "@/pages/Login";
import OAuthCallback from "@/pages/OAuthCallback";
import "./index.css";

/* 管理台独立会话:刷新/登出/me 走 /admin-auth,不与用户 kongming-web 共用 cookie。 */
configureAuthBase("/admin-auth");

const AdminLayout = lazy(() => import("@/pages/AdminLayout"));
const AdminIndexPage = lazy(() => import("@/pages/AdminIndexPage"));
const UsersPage = lazy(() => import("@/pages/UsersPage"));
const RolesPage = lazy(() => import("@/pages/RolesPage"));
const DeptsPage = lazy(() => import("@/pages/DeptsPage"));
const DatasourcesPage = lazy(() => import("@/pages/DatasourcesPage"));
const RagPage = lazy(() => import("@/pages/RagPage"));
const FeedbackPage = lazy(() => import("@/pages/FeedbackPage"));
const AuditPage = lazy(() => import("@/pages/AuditPage"));
const ToolsPage = lazy(() => import("@/pages/ToolsPage"));
const GuardrailsPage = lazy(() => import("@/pages/GuardrailsPage"));
const SemanticPage = lazy(() => import("@/pages/SemanticPage"));
const McpPage = lazy(() => import("@/pages/McpPage"));
const ConnectorsPage = lazy(() => import("@/pages/ConnectorsPage"));
const WorkflowsPage = lazy(() => import("@/pages/WorkflowsPage"));
const TriggersPage = lazy(() => import("@/pages/TriggersPage"));
const TeamsPage = lazy(() => import("@/pages/TeamsPage"));
const BotsPage = lazy(() => import("@/pages/BotsPage"));
const UsagePage = lazy(() => import("@/pages/UsagePage"));
const QualityPage = lazy(() => import("@/pages/QualityPage"));
const ControlPage = lazy(() => import("@/pages/ControlPage"));

function RouteSuspenseFallback() {
  return (
    <div className="p-4">
      <Skeleton className="h-96 w-full" />
    </div>
  );
}

function el(node: ReactNode): ReactNode {
  return <Suspense fallback={<RouteSuspenseFallback />}>{node}</Suspense>;
}

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
  { path: "/auth/keycloak/callback", element: <OAuthCallback /> },
  {
    path: "/",
    element: <Protected>{el(<AdminLayout />)}</Protected>,
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
        path: "teams",
        element: el(
          <PermRoute perm="system:team:manage">
            <TeamsPage />
          </PermRoute>,
        ),
      },
      {
        path: "control",
        element: el(
          <PermRoute perm="system:control:view">
            <ControlPage />
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
      { path: "*", element: el(<NotFoundPage />) },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
