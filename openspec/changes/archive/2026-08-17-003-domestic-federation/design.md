# 003 设计:国内三家联邦

## Context

002 已交付连接器框架:`OAuthProvider` 插件契约(key/display_name/pkce/configured/
authorize_url/fetch_profile)+ 注册表 + 通用路由 + options 数据驱动 + account_link
(identity 命中 → verified-email 关联 → 自动建号)。本变更只新增三个插件与配置,
三家协议差异必须止步于各自插件内(ADR-006)。002 留下的 rbac 措辞泛化、
simple-icons、favicon 三件收尾随本变更收编。

## Goals / Non-Goals

**Goals:**

- 企微/钉钉/飞书三路联邦登录可达(配置凭证后),行为与 002 立约完全一致;
  连接器内核、通用路由、account_link、openapi 契约零改动。
- 三家原始 JSON → NormalizedProfile 映射有纯函数单测锁死。

**Non-Goals:**

见 proposal(通讯录同步/应用分发/email 关联/IdP/内核扩张,均不做)。

## Decisions

### D1 协议适配:authlib 只构造授权 URL,token/profile 一律 httpx 直写

002 的 GitHub/Google 走 authlib `fetch_token` 标准流;三家均非标准形态,统一改在
插件内整体覆写 `fetch_profile`(基类设计预留的扩展点),用 httpx 直连:

| provider | authorize(扫码即 OAuth,无专用协议栈) | token/profile 形态 | quirks |
|---|---|---|---|
| 企微 wecom | `open.work.weixin.qq.com/wwopen/sso/qrConnect`(appid=corpid&agentid&redirect_uri&state) | 两步:`cgi-bin/gettoken`(corpid+corpsecret→企业 access_token)→ `cgi-bin/auth/getuserinfo`(access_token+code→UserId)→ 尝试 `cgi-bin/user/getuser` 取详档 | 详档需应用通讯录权限(ADR 注记);无权限时**降级** display_name=UserId,登录不受阻 |
| 钉钉 dingtalk | `login.dingtalk.com/oauth2/auth`(client_id&redirect_uri&response_type=code&scope=openid&state&prompt=consent) | `POST api.dingtalk.com/v1.0/oauth2/userAccessToken`(**JSON body**,非 form)→ `GET v1.0/contact/users/me`(userAccessToken 头) | 新版统一身份认证;旧版 app 签名机制不采用 |
| 飞书 feishu | `open.feishu.cn/open-apis/authen/v1/authorize`(app_id&redirect_uri&state) | `POST /open-apis/authen/v2/oauth/token`(JSON,app_id+app_secret+code)→ `GET /authen/v1/user_info`(Bearer user_access_token) | **v2 端点直接用 app 凭证换 user token,无需预取 app_access_token**——ADR-006「预取」注记是旧 v1 端点路径,采用 v2 并在 ADR-006 补演进注记(同 ADR-003 先例) |

三家 `pkce` 声明:默认 False(保守);apply 时以官方文档逐家核验,支持则开 True
(钉钉/飞书新端点均有 PKCE 形态,核验后定)。授权 URL 构造可复用基类
(声明式 auth_url + scope),仅 `fetch_profile` 覆写。

> **核验结论(2026-08-17 apply,官方文档)**:
> - 飞书:官方支持 PKCE → **pkce=True**;v2 token 请求体字段为 `client_id`/`client_secret`
>   (本表原写 app_id/app_secret,以官方为准);authorize 现行域名 `accounts.feishu.cn`。
> - 钉钉:统一身份认证文档无 PKCE 形态 → pkce=False;users/me 响应无 email 字段。
> - 企微:qrConnect 不支持 PKCE → pkce=False;getuserinfo 详档字段均为小写(userid/name/avatar)。
> - 企微/钉钉授权 URL 参数形态非纯标准(appid/agentid 命名、prompt=consent)→
>   两家覆写 `authorize_url`(基类 authlib 构造仅飞书复用),内核零改动。
> - 新增领域错误 `ProviderExchangeFailed`(provider 交换/拉取失败,经 AuthError 兜底
>   映射 400):三家非标准响应(200 带 errcode)需显式失败路径,内核/路由/传输模型不动。

### D2 profile 归一化:email 一律 `email_verified=False`

三家均不提供 email 验证语义 → 永不走 email 关联,account_link 只剩
「身份命中 / 自动建号」两支(spec「未验证 email 不关联」自动生效,防线不松)。
external_id 统一取**跨应用稳定标识**:钉钉/飞书用 unionId/union_id(不用 openId,
防同企业多应用身份分裂);企微用 UserId。username:企微=UserId、钉钉=nick、
飞书=en_name 兜底 name,冲突脱冲突走 002 既有 `_dedupe_username`。

### D3 配置分组

`KM_WECOM_CORPID / KM_WECOM_SECRET / KM_WECOM_AGENTID`、
`KM_DINGTALK_CLIENT_ID / KM_DINGTALK_CLIENT_SECRET`、
`KM_FEISHU_APP_ID / KM_FEISHU_APP_SECRET`;redirect_uri 各家独立
(默认 `http://localhost:5173/auth/{provider}/callback`,与 OAuth App 后台登记一致)。
`configured()` = 各组关键项齐备。`.env.example` 同步增补(含各平台后台登记指引注释)。

### D4 测试策略:normalize 纯函数直测,HTTP 交互不进测试

每家把「原始 JSON → NormalizedProfile」抽为纯函数 `normalize(...)`(企微含
详档缺失降级分支),固定 JSON 输入直测;`fetch_profile` 的 HTTP 编排不进测试。
account_link 全行为面(身份命中/自动建号/多身份/禁用)已由 002 fake provider
测试锁死,三家复用该机制不重测——新增仅 1.2 的 options 集成断言。

### D5 simple-icons 与 favicon

`pnpm add simple-icons`:登录页 `PROVIDER_MARKS` 改为 provider key → simple-icons
组件映射(五家全覆盖),删除 002 内联 SVG(例外条款随多品牌场景到期,单品牌缺失
仍可内联)。favicon:`apps/web/public/favicon.svg`(朱砂底 + 白色孔明意象,定稿从简)
+ `index.html` link,消除控制台 404。

### D6 契约零变化验证

`/auth/*` 路由与模型不动,openapi.json 预期零 diff;仍跑 `make contracts` 全管线
验证(防手滑改到响应模型)。

## Risks / Trade-offs

- [三家官方端点/字段口径随版本漂移] → apply 以官方文档为准逐家核验;
  normalize 单测把字段映射锁成回归网;漂移再发时改插件一处。
- [企微无通讯录权限时仅得 UserId,昵称为 ID 串] → 接受(登录链路不断);
  在 HANDOFF 与插件注释记明,引导管理员开权限。
- [无真实凭证无法三方 e2e] → 验收边界=normalize 单测 + options/404/start URL 形态冒烟;
  真实回调待用户提供凭证后人工过一遍(tasks 验收明记,不阻塞合并)。
- [PKCE 声明若与平台实际不符导致授权报错] → 默认 False 起步,核验后开,单项可回退。

## Migration Plan

无 schema 变更(0002 已五家建全)。部署:api 插件 + 配置 → web(simple-icons/favicon)。
回滚:删三个插件文件与注册表行即可,无数据面影响。

## Open Questions

- 钉钉/飞书 PKCE 支持情况:apply 时按官方文档核验后定(默认 False,不影响功能)。
