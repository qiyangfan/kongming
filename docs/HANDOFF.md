# HANDOFF — Kongming 交接文档

> 最后更新:2026-08-18(**004 执行路由真实化收官并归档 + 审查循环清零**:
> /internal/sql/execute 全链路成真——只读池/租户路由/data_scope 改写/审计三类落库;
> datascope 十条清单 + 17 全栈用例;审查循环 R1–R4 累计修 11 项后清零,api 120 测试全绿)。
> 清空上下文/新会话先读本文与 `AGENTS.md`,
> 动敏感区前按 AGENTS.md 前置阅读表读对应 ADR。**本文行文只记录"代码里看不出来"的事实。**

## 新会话开工指引(最先看这里)

1. 004 全闭账:执行路由(默认数据源)三态 verdict + 审计贯穿已立约(sql-execution spec);
   datascope 主 spec「包子查询」措辞已修正为「最外层 WHERE AST 注入」(实测证伪前者)。
   下一棒:**Agent LLM 接线(understand/generate/summarize)+ 回调 api 走通 →
   第一条端到端问答**(梯队见文末「下一步」),走 `/opsx:propose` 开局;
   回调报文形态见 `kongming/internal/router.py` 的 SqlExecuteRequest/Response。
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

## 当前状态(截至 004 提交,工作区干净)

- git:main 分支 6 提交(奠基/品牌/compose 收尾/002 认证闭环/003 五路联邦+审查硬化/
  **004 执行路由真实化+审查清零**,以 git log 为准),**工作区干净;无远程、未 push**
  (待用户指令)。**分支策略已定
  (2026-08-17 用户拍板):主干开发不拉 dev;未来协作时按变更开短命 feature 分支合回 main。**
- openspec:001/002/003/**004** 已归档;新增主 spec `sql-execution`(受控执行路由契约);
  datascope 主 spec 三处改写措辞修正(包子查询→最外层 WHERE 注入,列限定主表别名)。
- **执行路由(004)**:`POST /internal/sql/execute` 全链路——内部令牌 middleware(002 已有)
  → 租户解析(tenant_key,未知/禁用 404)→ 用户-租户绑定复校(未绑定 403;user_id 缺省
  =系统调用豁免)→ 非 default 数据源 400(动态池未落地,fail-closed 不静默回退)→
  防火墙初检 → data_scope 改写 → 防火墙复检 → 只读池执行(独立引擎,连接级
  `default_transaction_read_only=on` + 30s statement_timeout,池 5/0)+ 事务内
  `SET LOCAL search_path=tenant_<key>, public` + fetchmany 截断(默认 1000)。
  响应 verdict 三态:PASS/BLOCKED(策略拒绝,含 DATA_SCOPE_UNSUPPORTED)/ERROR
  (执行错误,reason=驱动层整洁摘要,已剥 SQLAlchemy asyncpg 包装类前缀,供 selfheal)。
- **datascope 域包**(004):models/service(纯逻辑)零框架;rewrite=sqlglot 最外层
  WHERE 注入 + 自定义方言子类保留 `:name` 绑定参数(postgres 方言默认渲 pyformat);
  值按列探测 data_type 类型感知绑定(uuid 列绑 UUID,PG 无 uuid=text 算子);
  repository=角色 scope 集/用户 dept_id/递归 CTE/information_schema 列探测
  (schema 限定 tenant_<key>+public)。多角色取最宽松序 ALL>CUSTOM>DEPT_AND_BELOW>
  DEPT>SELF(与上游 SMALLINT MIN 语义逐档对应);UNION/FROM 子查询取不到主表→安全降级;
  用户不存在/无角色→降级+告警(沿用上游裁定)。
- **审计(004)**:SqlAuditWriter 独立短事务写 sys_sql_audit,失败 error 日志不阻断;
  trace_id>64 截断告警;审计 sql_text=改写后实际执行形态(可回放)。
- schema:0001 baseline + 0002 user_identities + **0003 dept**(sys_dept 部门表
  UUID/parent_id 自引用/tenant_id FK + sys_user.dept_id;baseline 当初「dept_id 弃」
  =缓建,004 补齐)。
- **Makefile**:api/agent-check 已接线 `lint-imports`(原仅 CI 跑,本地门禁补齐);
  api 新增约定「datascope/audit 域禁 fastapi」(import-linter 需
  include_external_packages=true 才可禁外部包)。
- **004 审查循环(2026-08-18,R1 独立评审 7 项全属实已修 + R2 验收通过)**:
  ①未知租户「不落审计」测试断言恒真→改全表计数断言;②部门递归 CTE 改 UNION
  去重止环(脏数据成环曾实证挂到超时,控制面引擎无 statement_timeout);
  ③编排段基础设施异常(池超时等)收敛 verdict=ERROR+落审计,不再裸 500;
  ④**新增 schema 边界校验 check_schema_scope**:显式 schema 限定仅允许
  tenant_<key>/public(未加引号标识符按 PG 语义折叠小写比对,表函数/CTE 名放行)
  ——search_path 管不了显式限定名,跨租户显式引用此前可读(纵深缺口,spec 已补
  「租户 schema 边界」条款);⑤trace_id 请求宽度 200→64 与列同宽;
  ⑥清单 8 补子查询形态用例;⑦防火墙补拦 SELECT INTO(PG 建表写,Select 根放行
  的预存缺口,R1 自查发现)。R2 低级观察 2 项:大小写折叠误伤已修;
  schema 限定表函数跳过校验=刻意(本通道无法创建函数,无残余风险,代码注释登记)。
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
  qyf…-google/QiYangFan-wecom,均 ACTIVE,可作身份命中回归的活数据);
  **004 冒烟活数据**:`tenant_default.goods` 小表(4 行)、`smoke-self` 用户
  (SMOKE_SELF 角色=SELF,绑 default 租户,本人行=连弩)——可作执行路由回归活数据。
- 前端:登录页五家联邦按钮数据驱动;simple-icons 覆盖 GitHub/Google/微信系,
  **钉钉/飞书 simple-icons@16 未收录 → 按例外条款内联官方 mark**;favicon.svg 已补
  (朱砂底白色羽扇)。003 冒烟:五家图标实测渲染正确。
- 质量:api ruff/mypy strict/lint-imports/pytest **120**(认证全栈 68 + datascope 30 +
  执行路由 17 + 防火墙 5,真实 PG+Redis)+ agent 同门禁 6 + web tsc/eslint 全绿;
  CI api job 已带 pg/redis services(未在真实 runner 验证——push 后首验)。
- 契约:openapi.json 含 /auth/* + /internal/sql/execute(三态响应模型);
  004 模型落定后 `make contracts` 已重生成,web tsc 过。

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
| 执行路由 | 三态 verdict(PASS/BLOCKED/ERROR);用户-租户绑定复校;data_scope 改写=最外层 WHERE AST 注入(非包子查询);非 default 数据源 fail-closed | sql-execution + datascope spec(004) |

## 下一步(路线图梯队,按序)

1. Agent LLM 接线(understand/generate/summarize)+ 回调 /internal/sql/execute 走通
   → 第一条端到端问答(报文形态见 sql-execution spec 与 internal/router.py)。
2. 前端主布局(会话侧栏 + 对话区 + RBAC 菜单守卫;require_perm 依赖随首个受保护端点交付)。
3. RBAC 管理面 CRUD(用户/角色/权限点/部门,spec 已立约;sys_dept 存储底座 004 已建)。
4. 动态数据源注册/管理端点(sys_datasource 表已在,池管理器 key 维度与失效钩子已预留)。

## 已知 TODO(代码内标注,非缺陷)

agent graph 各节点 LLM;web 主布局;动态数据源池(sys_datasource 行驱动)。
**执行路由已闭账(004)**:唯一遗留=专用只读登录角色(km_readonly 为 NOLOGIN 组角色,
现以连接级只读+防火墙双闸替代;随动态数据源变更评估,见 004 design Open Questions)。
**联邦验收全部关闭**:五路真凭证 e2e 均过(2026-08-17)。
**账号绑定(登录态追加 provider)未立约**:同一人经多家联邦会各自建号(国内三家无
verified email 可关联),需绑定时另起提案。
**企微联调环境注记**:企微控制台指向 cloudflared 快速隧道域名(无拦截页,可信域名
校验兼容;隧道重启换域名后,可信域名/回调域 + KM_WECOM_REDIRECT_URI 三处同步改);
应用「企业可信 IP」白名单随家宽 IP 漂移重配。ngrok 免费固定域名与企微校验不兼容
(拦截页),勿用于企微。频繁联调或正式部署再上正式域名 + named tunnel。

## 命令速查

```bash
make check          # 全门禁(api/agent ruff+mypy+lint-imports+pytest + web tsc+eslint)
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
