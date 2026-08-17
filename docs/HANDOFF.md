# HANDOFF — Kongming 交接文档

> 最后更新:2026-08-17 晚(**003 国内三家联邦完整收官并已提交**:五路真凭证 e2e 全过 +
> 第二轮审查循环清零 + 联邦总开关落地)。清空上下文/新会话先读本文与 `AGENTS.md`,
> 动敏感区前按 AGENTS.md 前置阅读表读对应 ADR。**本文行文只记录"代码里看不出来"的事实。**

## 新会话开工指引(最先看这里)

1. 003 全闭账:五家插件(GitHub/Google/企微/钉钉/飞书)真凭证 e2e 全过;
   审查循环修复清零;企微经 `KM_WECOM_ENABLED=false` 软关停(凭证在,待正式域名)。
   下一棒:**/internal/sql/execute 执行路由**(梯队见文末「下一步」),走 `/opsx:propose` 开局。
2. 质量底线:安全规则类测试先行;收工前 `make check` 全绿 + 冒烟记录;
   改 api 必跑 `make contracts` 并提交产物。

## 项目一句话

全能 AI Agent 助手(数据问答/分析为首发王牌),Python 双进程 + React SPA。品牌 docs/brand.md。
前身 `/Users/solvela/nl2sql-data-platform`(Java COLA,38 变更,仍在运行,**不动它**)。
知识资产已平移(41 域 spec → 本仓库 16 域 + roadmap 登记 22 域)。

## 环境事实(新会话最易踩的坑)

1. **本机是 podman,不是 docker**:`docker` 只是交互 shell 的 alias,非交互命令里
   `docker` 不存在——一律用 `podman`。
2. **标准端口**:api **8000** / agent **8001** / web dev **5173** /
   postgres **5432** / redis **6379**(均可配置,见「命令速查」)。
   **注(2026-08-17 晚实测):旧项目 nl2sql 已全面停用(容器与 vite 裸进程均已退),
   Kongming web dev 收回 5173**;两家联邦控制台回调均已按 5173 登记。
3. **本地环境冷启动**(幂等,任何"连不上/端口不对"先跑这个):
   ```bash
   make up                                 # podman compose 起 postgres+redis(默认端口)
   cd apps/api && uv run alembic upgrade head
   ```
   pg 数据持久化在 compose 卷 `kongming_pg_data`;删卷/重置属破坏性操作,须用户明确授权。
4. 三处 `.env` 已配好(不入库):根(占位密钥)、`apps/api/.env`(默认端口连接串 +
   `KM_INTERNAL_TOKEN=dev-internal-token`)、`apps/agent/.env`(同内部令牌;LLM key=dummy)。
5. 可能的后台进程:api(8000,带真联邦凭证)/agent(8001)/web(5173);
   另有 ngrok 固定域名隧道(baggie-armadillo-crave.ngrok-free.dev → 5173,演示用)
   与用户自建的 cloudflared 系统服务隧道(挂起 zone,无效但无害)。日志 /tmp/km-*.log;
   重启 Kongming 自身进程前先清理(如 `pkill -f "uvicorn kongming"`、
   `pkill -f "kongming/apps/web"`;ngrok/cloudflared 服务进程不属本项目,别动)。
6. 本地 dev 流程:基础设施(pg/redis)走 `make up`(podman compose 委托 docker-compose 二进制,
   已实测);两个 Python 服务日常 dev 直接 `uv run uvicorn ...`(compose 的 api/agent
   服务留给完整部署用)。
7. **浏览器实测**:ZCode IAB 面板 input 管道(click/press/screenshot)对本机 guest 会卡死
   (DOM 快照可读)——GUI 测试一律用 Playwright MCP 工具组
   (`mcp__plugin_playwright_playwright__browser_*`,独立管道,002 已验证可用)。

## 当前状态(截至 003 归档,工作区改动待提交)

- git:main 分支 5 提交(奠基/品牌/compose 收尾/002 认证闭环/**003 五路联邦+审查硬化**,
  以 git log 为准),**工作区干净;无远程、未 push**(待用户指令)。**分支策略已定
  (2026-08-17 用户拍板):主干开发不拉 dev;未来协作时按变更开短命 feature 分支合回 main。**
- openspec:001/002/003 已归档(rbac 主 spec 已泛化为 provider 无关「联邦账号权限一致」)。
- 认证(ADR-003/006 落地):账密登录(argon2id + 失败 5 次锁 15min + 禁用/软删拒登 +
  首登强制改密)、refresh 家族轮换与复用吊销(Redis 三态)、logout、/auth/me 真实
  角色/权限点/租户、自助改密(吊销全家族)、连接器框架 + **五路插件** GitHub/Google/
  企微/钉钉/飞书(state 一次性 + PKCE 按家声明 + verified-email 关联 +
  自动建号绑 DEVELOPER/default 租户)。
  **003 协议核验结论(官方文档,ADR-006 演进注记)**:飞书 v2 端点 client_id/client_secret
  直换 user token(免预取 app_access_token)且官方支持 PKCE → pkce=True;
  钉钉/企微无 PKCE 形态 → False;企微详档需通讯录权限,缺失降级 display_name=UserId;
  三家 email 一律不入管线(无验证语义)。provider 交换失败新增 ProviderExchangeFailed→400。
  **真实 e2e 五路全过(2026-08-17,真凭证全链路)**:**钉钉**✓(建号→DEVELOPER/default→
  unionId 绑定→日志齐);**飞书**✓(PKCE 实跑);**GitHub**✓(基类标准 authlib 流首验,
  email 私有未回传→不落库,verified-only 纪律实证);**Google**✓(基类 PKCE+OIDC 首验,
  verified email 落库分支实证);**企微**✓(cloudflared 快速隧道过可信域名校验;详档
  权限已开→name/avatar 全字段路径实证,降级路径单测覆盖)。
  e2e 抓出并修复:飞书 v2 token 必带 redirect_uri(官方标注可选实为必带);
  conftest 三家默认置空串防本地 .env 真凭证渗入测试面。
  **企微两个平台侧门槛(控制台操作,非代码)**:①可信域名需归属校验(校验文件放域名
  根目录,本次经隧道域名完成);②gettoken 要求调用方出口 IP 在应用「企业可信 IP」
  白名单(60020),家宽 IP 漂移需重配。
  **联邦总开关**:`KM_<PROVIDER>_ENABLED`(五家,默认 true)=false 即停用该家
  (options 隐藏 + start/callback 404),凭证保留——企微现已用 `KM_WECOM_ENABLED=false`
  关停,待正式域名/可信 IP 环境就位再翻回。
  **企微联调环境结论(2026-08-17 晚)**:ngrok 免费固定域名与企微可信域名校验**不兼容**
  (校验爬虫带浏览器 UA,撞上免费版拦截页);可用路径 = cloudflared 快速隧道
  (无拦截页,已实证)或正式域名 + named tunnel。ngrok 固定域名
  (baggie-armadillo-crave.ngrok-free.dev)保留作对外演示入口。
  **联邦建号用户名一律 {base}-{provider}(无冲突也带后缀,来源可溯,用户拍板)**——
  spec「冲突时生成不重复用户名」粒度以下的实现变更,不涉及约;脱冲突 -N 链保留。
  钉钉身份命中分支亦真人复验(再登只见 login、用户数不变)。
  **轮换判定已原子化(Lua GETDEL+立墓碑单脚本)**:并发同票刷新仅一个胜者,败者按
  spec「复用即吊销家族」处理——多标签页毫秒级同票竞刷会触发重新登录,属契约既定语义;
  前端 bootstrap 已登录态短路,路由重挂载不再重复轮换(防误触发)。
- 评审循环(002 提交前):核验出 6 真实缺陷全部修复(轮换原子化/0002 downgrade 五家 CHECK/
  config 下界/角色 sort 落库/bootstrap 短路/杂项),剔除全部虚构项;54 测试全绿。
- 启动幂等种子(lifespan):角色 SUPER_ADMIN/DEVELOPER、权限点注册表(system:user|role:manage)、
  默认租户 default、超管 admin(KM_ADMIN_INITIAL_PASSWORD 缺省 admin123 强制首登改密)。
- **dev 库现状**:冒烟后 admin 密码已被改为 `Kongming#2026`(非缺省;如需复位可删库重播或走改密接口);
  角色 sort 列为 0/0(种子只增不改,不补齐历史行;新库播种即 1/2);
  另有五路联邦 e2e 留下的 5 个联邦账号(漆阳帆-dingtalk/漆阳帆-feishu/qiyangfan-github/
  qyf…-google/QiYangFan-wecom,均 ACTIVE,可作身份命中回归的活数据)。
- schema:0001 baseline + 0002 user_identities(sys_user.auth_source/external_id 已删列收敛);
  003 零迁移(0002 已五家建全 CHECK)。
- 前端:登录页五家联邦按钮数据驱动;simple-icons 覆盖 GitHub/Google/微信系,
  **钉钉/飞书 simple-icons@16 未收录 → 按例外条款内联官方 mark**;favicon.svg 已补
  (朱砂底白色羽扇)。003 冒烟:五家图标实测渲染正确。
- 质量:api ruff/mypy strict/import-linter/pytest 70(认证全栈集成测试,真实 PG+Redis,
  含三家 normalize 回归网 + options/start 形态断言 + enable 开关/后缀建号/超长截断用例)
  + agent 同门禁 6 + web tsc/eslint 全绿;
  CI api job 已带 pg/redis services(未在真实 runner 验证——push 后首验)。
- 契约:openapi.json 含 /auth/* 全部端点;003 路由/模型零改动,`make contracts` 实测零 diff。

## 已定死的决策(不再议,引用即终)

| 主题 | 结论 | 出处 |
|---|---|---|
| 技术栈 | Python 3.13 双进程(api 持凭证 / agent 零凭证)+ React 19 SPA;不引入 Keycloak/Next.js/微服务/消息队列/Litestar | ADR-001 |
| 信任边界 | 进程边界构造级;agent 禁 DB 凭证(env/驱动双禁);合并进程=违宪 | ADR-002 + 宪法 Article IV |
| 认证 | 自建(argon2id + PyJWT RS256 + authlib);登录页=前端路由;未来企业 SSO 走"引入 IdP 作下游"回退路径 | ADR-003 |
| 联邦登录 | 连接器架构:通用 OAuth2 内核 + provider 插件 + 通用路由 + options 数据驱动;新增 provider=插件文件+配置,路由/前端零改动 | ADR-006 |
| Schema | baseline 制;UUID v7 / VARCHAR+CHECK / timestamptz;已规划能力列一次建全 | ADR-004 |
| 契约 | FastAPI OpenAPI 唯一事实源;前端类型生成禁手写;CI 漂移 diff | ADR-005 |
| 分层 | 域包内 router→service→models→repository;domain 零框架依赖 | 宪法 Article II |

## 下一步(路线图梯队,按序)

1. `/internal/sql/execute` 执行路由:数据源连接池(默认 default 库即可起步)+
   审计落库 + data_scope 改写接入(datascope spec 测试清单 10 条先行)。
2. Agent LLM 接线(understand/generate/summarize)+ 回调 api 走通 → 第一条端到端问答。
3. 前端主布局(会话侧栏 + 对话区 + RBAC 菜单守卫;require_perm 依赖随首个受保护端点交付)。
4. RBAC 管理面 CRUD(用户/角色/权限点/部门,spec 已立约)。

## 已知 TODO(代码内标注,非缺陷)

`internal/router.py` 执行路由;agent graph 各节点 LLM;web 主布局。
**联邦验收全部关闭**:五路真凭证 e2e 均过(2026-08-17)。
**账号绑定(登录态追加 provider)未立约**:同一人经多家联邦会各自建号(国内三家无
verified email 可关联),需绑定时另起提案。
**企微联调环境注记**:企微控制台指向 cloudflared 快速隧道域名(无拦截页,可信域名
校验兼容;隧道重启换域名后,可信域名/回调域 + KM_WECOM_REDIRECT_URI 三处同步改);
应用「企业可信 IP」白名单随家宽 IP 漂移重配。ngrok 免费固定域名与企微校验不兼容
(拦截页),勿用于企微。频繁联调或正式部署再上正式域名 + named tunnel。

## 命令速查

```bash
make check          # 全门禁(api/agent ruff+mypy+pytest + web tsc+eslint)
make contracts      # 导出 OpenAPI + 生成前端类型(改 api 后必跑)
# 服务: apps/api → uv run uvicorn kongming.main:app --port 8000
#       apps/agent → uv run uvicorn kongming_agent.main:app --port 8001
#       apps/web → pnpm dev(5173,proxy /api → 8000)
# 迁移: apps/api → uv run alembic upgrade head
# 冒烟: curl 127.0.0.1:8000/healthz;curl 127.0.0.1:8001/healthz
```

## 铁律提醒(全局,详见 ~/.zcode/AGENTS.md)

- git 写操作(add/commit/push)逐次等用户明确指令,只做字面授权那一步。
- 旧项目容器/卷/网络禁碰;破坏性操作前二次确认。
- 安全规则类改动测试先行;文档注释一律简体中文。
