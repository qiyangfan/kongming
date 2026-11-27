import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, type ReactNode, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import { AppLayout } from "@/components/app-layout";
import { PermRoute } from "@kongming/ui/perm-route";
import { Skeleton } from "@kongming/ui/skeleton";
import Login from "@/pages/Login";
import OAuthCallback from "@/pages/OAuthCallback";
import { NotFoundPage } from "@kongming/ui/error-pages";
import "./index.css";

/* 首屏外路由懒加载；登录页与公开 Bot 嵌入页保持静态导入。 */

const MemoryPage = lazy(() => import("@/pages/MemoryPage"));
const ReportsPage = lazy(() => import("@/pages/ReportsPage"));
const ReportDetailPage = lazy(() => import("@/pages/ReportDetailPage"));
const TasksPage = lazy(() => import("@/pages/TasksPage"));
const TaskDetailPage = lazy(() => import("@/pages/TaskDetailPage"));
const StorePage = lazy(() => import("@/pages/StorePage"));
const ChatPage = lazy(() => import("@/pages/ChatPage"));
const BotPublicPage = lazy(() => import("@/pages/BotPublicPage"));

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
