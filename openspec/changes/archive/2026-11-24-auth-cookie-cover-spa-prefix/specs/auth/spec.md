## ADDED Requirements

### Requirement: 认证 Cookie 覆盖 API 前缀

登录发起与回调下发的 state cookie、登录成功后下发的 refresh cookie SHALL 在浏览器
经 `/api` 前缀访问认证端点时被携带;清除这些 cookie 的指令 SHALL 使用与下发一致的
作用路径。该要求不改变 cookie 名、有效期、HttpOnly、SameSite 与 Secure 策略。

#### Scenario: 经 API 前缀完成回调与刷新

- GIVEN 浏览器经 `/api` 前缀发起登录并在 `/api/auth/keycloak/callback` 提交 state
- WHEN 服务端下发 state 与 refresh cookie
- THEN 后续 `/api/auth/refresh` 请求携带 refresh cookie

#### Scenario: 直连后端路径不受影响

- GIVEN 非浏览器客户端直接请求 `/auth/*`
- WHEN 携带同一 cookie
- THEN 认证流程照常接受该 cookie
