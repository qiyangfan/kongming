# 认证 Cookie 覆盖 SPA 的 /api 前缀

## Why

前端所有 API 调用统一经 `/api` 前缀(dev 由 Vite 代理重写到后端根路由),而登录
state cookie 与 refresh cookie 原先的作用路径是 `/auth`。浏览器按 RFC 6265 做路径
匹配,`/api/auth/*` 请求既不携带 state cookie 也不携带 refresh cookie:回调报
「state 未绑定当前浏览器」,登录后启动刷新 401。API 级联调直连 `/auth/*` 不经过
前缀,因此该缺陷只在浏览器路径暴露。

## What Changes

- state cookie 与 refresh cookie 的作用路径改为 `/`,同时覆盖 `/auth/*`(直连)与
  `/api/auth/*`(浏览器);对应的清除指令使用同一路径。
- 新增回归测试:按 RFC 6265 path-match 断言两类 cookie 均可被 `/api/auth/*` 携带,
  旧实现失败、新实现通过。

## Capabilities

### New Capabilities

### Modified Capabilities

- `auth`:认证 cookie(登录 state、本地 refresh)在 SPA 的 API 前缀路径下可被携带。

## Impact

- 浏览器端 OIDC 回调与刷新恢复;直连 `/auth/*` 的非浏览器客户端行为不变。
- 对外接口、字段与错误码不变;cookie 名、有效期、HttpOnly、SameSite、Secure 不变。
- refresh cookie 作用域扩大到 API 同源全部路径;同源信任边界下接受,若后续同源挂载
  其他应用再评估限定方案。

## Non-goals

- 不改前端 API 前缀与代理拓扑。
- 不改 state 多值列表语义、refresh 轮换与复用检测。
- 不改登出与统一登出流程。
- 不引入双 cookie 或依赖反代改写 Cookie 路径的方案。

## ADR

- ADR-003:本地会话与 refresh 只经 HttpOnly cookie 往返。
- ADR-006:Keycloak 作为唯一 OIDC provider 的接入形态。
