# 任务:孔明可选联邦独立 IdP(镜像)

> 安全规则类(认证/权限/租户)测试先行:先写失败测试再实现。每 task 完成跑 make check 对应子集并在 apply 记录。
>
> **本轮(2026-11-20)处置:owner 已批准「可选联邦」方向,实现类条目仍「待实施」(6.2 ADR 同步已落笔)。** 阻塞依据:① 独立 IdP
> 项目(虎符 `longzhong/hufu`)尚未交付 issuer/JWKS/授权码端点,无集成对象与联调环境;② 联邦为可选增强,
> 在 IdP 不可用时不得引入任何对本地认证的硬依赖。解除条件:虎符交付授权码 + PKCE 流程与 JWKS 端点,
> 且 owner 明确批准进入实施。

## 1. 可选 provider 接入与验签

- [ ] 1.1 先写失败测试:未配置 IdP provider 时登录清单/行为不变;启用后授权码 + PKCE 经 IdP 登录、JWKS 验 id_token(RS256 + iss/aud/exp/nonce,主体取 `sub`)、aud/nonce 不符拒绝、API 不采信 IdP access token
- [ ] 1.2 复用联邦连接器架构(ADR-006)把 IdP 作为可选 provider 接入;只消费标准 OIDC 端点(发现文档/JWKS/授权码/token),无私有端点;token 只后端存活
- [ ] 1.3 联邦回调验 id_token 后仍由本地签发会话令牌;本地签发/验签路径零改动
- [ ] 1.4 make check 子集绿

## 2. 影子映射

- [ ] 2.1 先写失败测试:IdP `sub`→本地 `idp_subject` 映射、首登 JIT 建档案 + 默认角色/租户、资产不 orphan
- [ ] 2.2 sys_user 加 `idp_subject` 映射列(核对 ADR-004 baseline,只增迁移)
- [ ] 2.3 JIT 建影子档案(本地授权动作)
- [ ] 2.4 make check 子集绿

## 3. 本地认证保留与并存

- [ ] 3.1 先红后绿:启用 IdP provider 后,本地账密/自助改密/OAuth 5 家/refresh 家族行为与变更前一致
- [ ] 3.2 关闭 IdP provider 后,登录清单与行为回落本地,无残留依赖
- [ ] 3.3 web 登录页在 provider 已配置时呈现「统一身份登录」入口;未配置时不出现
- [ ] 3.4 make check 子集绿

## 4. 授权留本地回归

- [ ] 4.1 me 在当前凭据轨验签后返回本地角色/权限/租户;账号状态实时核查
- [ ] 4.2 require_perm 192 处 / 租户白名单 / billing/portal 零改回归
- [ ] 4.3 make check 子集绿

## 5. 联邦登出联动(RP-Initiated Logout)

- [ ] 5.1 先写失败测试:IdP 已配置时登出后跳转 `end_session_endpoint`(含 client_id 与登出回跳地址,不含任何令牌);未配置/发现文档不可用或缺端点时回落地登录页;本地 refresh 家族吊销不受影响
- [ ] 5.2 新增 `GET /auth/logout/sso`(本地登出后由 web 整页导航);新增登出回跳地址配置;`POST /auth/logout` 保持 204
- [ ] 5.3 make check 子集绿

## 6. 联调与收口

- [ ] 6.1 与虎符联调:授权码 + PKCE 全链、跨域 SSO、登出联动、关闭 provider 回落本地
- [x] 6.2 ADR-003/006/007 更新(已写入发布与互联边界;ADR-006 记录 IdP provider 只消费标准 OIDC 契约)
- [ ] 6.3 spec deltas sync 进 specs/;聚合层 HANDOFF 回写
- [ ] 6.4 make check 全绿 + 归档
