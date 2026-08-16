import { Navigate } from "react-router";

export default function App() {
  // TODO: 主布局(侧栏会话列表 + 对话区 + RBAC 菜单),登录态守卫
  return <Navigate to="/login" replace />;
}
