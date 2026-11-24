# Tasks

## 1. 测试先行

- [x] 1.1 新增 `test_auth_cookies_cover_spa_api_prefix`:按 RFC 6265 path-match 断言
  state 与 refresh cookie 可被 `/api/auth/*` 携带;对旧实现(Path=/auth)跑出失败,修复后通过。

## 2. 实现

- [x] 2.1 state cookie 与 refresh cookie 的作用路径改为 `/`;回调清 state、登出清 refresh
  使用同一路径;注释说明 `/api` 前缀来源。

## 3. 验收

- [x] 3.1 认证测试三文件全绿(47 passed);ruff、pyright 通过。
- [x] 3.2 浏览器三端 SSO 复测 9/9(统一身份栈),协议级 e2e ALL PASS。
- [x] 3.3 全量 api-check 结果记录到 HANDOFF(数量以命令输出为准)。

## 4. 收口

- [x] 4.1 `openspec validate --all --strict` 与 `--specs --strict` 通过;HANDOFF 回写。
