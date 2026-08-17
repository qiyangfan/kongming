# 003 Tasks — 国内三家联邦

> 纪律:插件字段映射测试先行(normalize 纯函数先写失败测试);
> 每完成一组跑对应 `make check` 子集并在此记录结果。

## 1. 测试先行

- [x] 1.1 企微 normalize 单测:UserId/name/avatar 映射 + 详档缺失降级(display_name=UserId)
- [x] 1.2 钉钉 normalize 单测:unionId/nick/avatarUrl;external_id 用 unionId 不用 openId
- [x] 1.3 飞书 normalize 单测:union_id/name(en_name 兜底)/avatar_url
- [x] 1.4 options 集成断言:monkeypatch 配置三家后 /auth/options 出现三家、start URL 形态正确;
      未配置维持 404(002 语义回归)

## 2. 三家插件

- [x] 2.1 config 三组配置(wecom corpid/secret/agentid、dingtalk client_id/secret、
      feishu app_id/secret)+ 各自 redirect_uri 默认值;`.env.example` 增补登记指引
- [x] 2.2 providers/wecom.py:qrConnect 授权 URL + httpx 两步(gettoken→getuserinfo→尝试 getuser)
- [x] 2.3 providers/dingtalk.py:统一身份认证授权 URL + JSON body token + users/me 拉取
- [x] 2.4 providers/feishu.py:authorize URL + v2/oauth/token(JSON)+ v1/user_info 拉取
- [x] 2.5 注册表 + 三家 PKCE 声明按官方文档核验后定(默认 False);`make check` api 子集绿

> 2.5 核验结论(2026-08-17,官方文档):企微无 PKCE=False;钉钉统一认证文档无 PKCE 形态=False;
> 飞书官方支持 PKCE(authorize code_challenge + token code_verifier)=**True**。
> 另两处以官方为准修正:飞书 v2 token 请求体用 client_id/client_secret(非 app_id/app_secret);
> 飞书 authorize 现行域名为 accounts.feishu.cn。api 门禁:ruff/mypy/66 pytest 全绿。

## 3. 前端收尾

- [x] 3.1 `pnpm add simple-icons`;登录页 PROVIDER_MARKS 改 simple-icons 五家映射,删内联 SVG
- [x] 3.2 `apps/web/public/favicon.svg` + index.html 引用(朱砂底白色孔明意象,从简)
- [x] 3.3 `make check` web 子集绿;浏览器实测登录页五家图标渲染(配置三家后)

> 3.1 实测注记:simple-icons@16.28.0 收录 GitHub/Google/微信(企业微信无独立图标,
> 微信官方族图标代位);**钉钉/飞书未收录** → 按 D5 例外条款内联官方 mark
> (取自 Logto connectors 官方 logo.svg)。3.3 实测:五家图标渲染正确(截图存证),
> favicon 200 且控制台 404 噪音消除。

## 4. 文档与验证

- [x] 4.1 ADR-006 补演进注记:飞书采用 v2 端点(免预取 app_access_token)、企微详档降级路径
- [x] 4.2 全量 `make check` 绿;`make contracts` 验证 openapi.json 零 diff(路由/模型未动)
- [x] 4.3 冒烟:未配置三家 404;配置后 options 出现、start URL 形态(qrConnect/统一认证/authorize)
- [x] 4.4 rbac spec delta sync 进主 spec(归档时)+ 归档 003
- [x] 4.5 HANDOFF 更新(当前状态/下一步梯队:执行路由 → Agent LLM → 主布局 → RBAC 管理面)

## 验收

- [x] 三家 normalize 单测绿(字段映射回归网);options/404 语义与 002 一致
- [x] openapi.json 零 diff;web 五家图标浏览器实测渲染正确
- [ ] 真实三方回调链路:待用户提供至少一家凭证后人工 e2e 一遍(不阻塞合并,记 HANDOFF 待办)
