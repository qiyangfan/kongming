# 任务:孔明去账号化(虎符单一身份)

> 原立项为「可选联邦」;owner 定稿 ADR-0010 后改为单一账户:孔明不再保留本地
> 登录/密码/5 家 provider,`sys_user.id` 直接为虎符 `sub`。2026-11-21 完成。
> 另:0066 迁移退役旧本地/社交影子行(软删+解绑),不再持有授权。

## 1. 唯一登录入口

- [x] 1.1 失败测试:本地 `/auth/login`、改密与 5 家 provider 路径删除;未配置虎符时入口不出现
- [x] 1.2 只注册虎符 OIDC provider(发现/JWKS/授权码+PKCE/nonce/state 单次)
- [x] 1.3 回调验 id_token 后签本地双 token;refresh/me 语义保持
- [x] 1.4 auth/federation/hufu 子集绿

## 2. subject 主键与迁移

- [x] 2.1 测试:首登 `sys_user.id == id_token.sub`,不按 email/username 合并
- [x] 2.2 迁移 0065 删除密码列;主体键复用 `sys_user.id`,不新增影子映射列
- [x] 2.3 首登 JIT 只建授权资料(DEVELOPER/default 租户),资产按 id 关联
- [x] 2.4 auth/seed/迁移子集绿

## 3. 删除本地认证

- [x] 3.1 删除本地账密/自助改密/登录锁定与 5 家 provider 代码和测试
- [x] 3.2 种子不再创建可登录账号;`KM_ADMIN_SUBJECT` 只预建授权资料与 HUFU identity
- [x] 3.3 web 登录页只显示「统一身份登录」;管理用户页删除新建/改密入口
- [x] 3.4 web typecheck/lint/test/build/e2e 绿

## 4. 授权留本地

- [x] 4.1 `/auth/me` 按 subject 返回本地角色/权限/租户,禁用实时核查
- [x] 4.2 require_perm/租户白名单/billing/portal 回归通过
- [x] 4.3 api-check 全绿(1684 passed,覆盖率 76.97%)

## 5. 登出与联调

- [x] 5.1 refresh 家族吊销/登出语义保留
- [x] 5.2 虎符真实联调:授权码+PKCE,`/auth/me` user_id 等于虎符 sub
- [x] 5.3 契约与前端类型再生成
