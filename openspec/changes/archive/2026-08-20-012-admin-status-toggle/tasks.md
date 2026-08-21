# 012 管理台状态启停范式统一 — 实施任务

> 安全面(权限/保护规则/吊销)测试先行:先写失败测试再实现。
> 每完成一组跑对应 make check 子集;动 api 路由后跑 make contracts。

## 1. api:用户启停端点与禁用吊销(测试先行)

- [x] 1.1 失败测试先行——service 用例:disable 吊销该用户全部 refresh 家族(旧 refresh 换 token 401)、enable 不吊销、update_user 走 status→DISABLED 分支同吊销(两路同收口直证)、自禁 409、超管账号禁用 409、目标不存在 404、审计 user.enable/user.disable 落库;跑红
- [x] 1.2 system service 实现:私有单点 `_assert_disable_allowed`(自禁/超管保护)+`_revoke_refresh`(吊销管线,改密/重置/禁用三路同调);专用 `enable_user`/`disable_user` 与 `update_user` 的 status 分支同调(R2-F10 收口:保护与吊销零复制);测试转绿
- [x] 1.3 失败测试先行——端点用例:POST /system/users/{id}/enable|disable 权限 403(无 system:user:manage)/未认证 401(参数化直测,R2-F11)/404/自禁 409/超管 409/成功 204;跑红
- [x] 1.4 system router 挂 2 端点(require_perm system:user:manage);审计动作清单测试实际调用两端点并扩 expected 集合(user.enable/user.disable,R2-F4);测试转绿;`make check` api 子集绿

## 2. web:StatusSwitch 公共组件

- [x] 2.1 `pnpm dlx shadcn@latest add switch` 引入;006 R4 纪律核验语义类(轨道/手柄/聚焦环 token 在 index.css 有定义、radix 聚合包零新依赖);tsc/eslint 绿
- [x] 2.2 新建 `components/status-switch.tsx`:`StatusSwitch({active,pending,error,onToggle,ariaLabel})`——受控 checked=active 真值、pending 禁用、error 行内 role=alert(自动消失交父层策略);tsc/eslint 绿

## 3. 两页接入

- [x] 3.1 UsersPage:状态列 Badge→StatusSwitch;行级 pending/error state+启停 mutation(调新端点);成功 invalidate 列表;失败回弹+文案;编辑对话框 Select 保留;tsc/eslint 绿
- [x] 3.2 DatasourcesPage:状态列 Badge+行内文字按钮→StatusSwitch(调既有 enable/disable 端点);删除旧按钮与其错误态代码;tsc/eslint 绿
- [x] 3.3 system-api.ts 加 enableUser/disableUser 封装;contracts 类型再生成确认覆盖

## 4. 契约、门禁与冒烟

- [x] 4.1 `make contracts` 重生成(+2 端点)并确认 web tsc 过;`make check` 全量绿
- [x] 4.2 Playwright MCP 冒烟:用户页开关启停(badge 列翻转为开关真值)/禁用 smoke-self 类用户后其旧 refresh 失效(curl 复验 401)/自禁 409 开关回弹+错误可见/超管行禁用 409 文案/数据源页开关形态一致+失败回弹(可用改错密路径构造)/对话框 Select 仍在
- [x] 4.3 HANDOFF.md 更新(012 状态/挂账/冒烟记录)
