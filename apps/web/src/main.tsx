import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import App from "./App.tsx";
import ChangePassword from "./pages/ChangePassword.tsx";
import Login from "./pages/Login.tsx";
import OAuthCallback from "./pages/OAuthCallback.tsx";
import "./index.css";

const queryClient = new QueryClient();

const router = createBrowserRouter([
  {
    path: "/login",
    element: <Login />,
  },
  {
    path: "/auth/:provider/callback",
    element: <OAuthCallback />,
  },
  {
    path: "/change-password",
    element: <ChangePassword />,
  },
  {
    path: "/",
    element: <App />,
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
