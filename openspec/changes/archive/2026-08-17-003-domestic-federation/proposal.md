# 003 国内三家联邦(企微/钉钉/飞书)

## Why

ADR-006 实施节奏明确:002 交付连接器框架 + GitHub/Google 首发,003 接入国内三家
(企业微信/钉钉/飞书),五路 OAuth 联邦拼图完成。002 的连接器架构(通用内核 +
插件注册表 + 通用路由 + options 数据驱动)已证明扩展点成立——本变更新增三家 =
三个声明式插件 + 配置项,路由与前端交互零改动;同时收编 002 明确留下的三件
收尾:rbac spec 措辞泛化、simple-icons 上包、favicon。

## What Changes

- **企业微信插件**:扫码即 OAuth(授权页自带二维码,无专用协议栈——ADR-006 关键设计 4);
  两步令牌(先 corpid+corpsecret 换企业 access_token,再以 access_token+code 查用户),
  profile 归一化(email 无验证语义,一律走身份命中/自动建号,不走 email 关联)。
- **钉钉插件**:新版统一身份认证(OIDC 形态);token 端点为 JSON 请求体(非标准 form),
  插件内 HTTP 直写,authlib 仅承担授权 URL 构造。
- **飞书插件**:app_access_token 预取(ADR 既定);user_access_token 交换与 userinfo 拉取
  同走 HTTP 直写。
- **配置**:`KM_WECOM_*` / `KM_DINGTALK_*` / `KM_FEISHU_*` 三组凭证与 redirect_uri;
  `.env.example` 同步;未配置即隐藏(options/404 语义已在 002 立约,不变)。
- **user_identities 零迁移**:0002 已按五家一次建全 CHECK(ADR-004),本变更无 schema 改动。
- **simple-icons 上包**:五家品牌 logo 统一走 simple-icons(ADR-006 品牌条款),
  替换 002 的内联 SVG(铁律例外条款随多品牌场景到期)。
- **favicon**:补位(消除控制台 404 噪音)。
- **rbac spec 泛化**:「OAuth(GitHub)账号权限一致」改写为 provider 无关的
  「联邦账号权限一致」(002 proposal 既定留待本变更)。

## Capabilities

### New Capabilities

无。三家接入的行为契约(state/PKCE/account_link/自动建号/options 过滤/404)
已由 auth spec 以 provider 无关形态完整立约(002),插件差异是协议实现细节,
不产生新的平台级行为。

### Modified Capabilities

- `rbac`: 「OAuth(GitHub)账号权限一致」泛化为 provider 无关——五路联邦账号在
  权限体系内与本地账号完全一致(权限点、行级 data_scope、租户白名单无差异)。

## Non-goals

- 不改 auth spec 任何现有要求(002 已是 provider 无关形态;不重开立约)。
- 不做企微通讯录同步/组织架构对接、钉钉/飞书的应用市场分发或机器人能力——仅登录联邦。
- 不做三家 email 关联(各平台均不提供 email 验证语义,spec「未验证 email 不关联」
  分支自动生效;防邮箱伪造劫持的防线不松)。
- 不引入自托管 IdP、不加 SAML/LDAP(ADR-003/006 已否决,回退路径不变)。
- 不动连接器内核与通用路由(若三家接入暴露内核缺口,先记 Open Question 评估,
  不顺手扩架构)。

## Impact

- **代码**:`apps/api/kongming/auth/providers/` 新增 wecom.py / dingtalk.py / feishu.py
  + 注册表三行;`config.py` 三组配置;base.py 预计零改动(插件可整体覆写
  `fetch_profile`,内核不感知协议差异)。
- **测试**:三家 normalize 单测(固定 JSON → NormalizedProfile)+ 经 fake provider 的
  account_link 复用 002 既有测试面;安全语义(锁定/轮换/state)不重测(002 已锁死)。
- **前端**:`apps/web` 新增 simple-icons 依赖,登录页五家 mark 数据驱动映射;
  favicon 一枚。
- **契约**:`/auth/*` 端点零变化,openapi.json 预期无 diff(仍跑 `make contracts` 验证)。
- **依赖**:web 新增 simple-icons(dev 侧零运行时后端依赖;三家 HTTP 直写复用 httpx)。
- **ADR 引用**:003(自建认证)/ 006(连接器架构;本变更完成其实施节奏第二刀)。
