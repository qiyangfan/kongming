/* 管理面占位(RBAC CRUD 属下一变更):仅证 403 守卫与菜单通路,不渲染功能。 */

import { Settings2 } from "lucide-react";

export default function AdminHome() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <Settings2 className="size-10 text-primary" aria-hidden />
      <h2 className="text-lg font-semibold text-foreground">管理面</h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        用户/角色/权限/部门管理正在路上——此处已按权限点守卫,
        具体功能随下一变更交付。
      </p>
    </div>
  );
}
