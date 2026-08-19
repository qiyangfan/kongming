# Kongming 技术栈锁定(宪法)

> 任何偏离需新增 ADR 并修订本文件。版本升级(同 major)允许,换库需 ADR。

## 运行时

| 组件 | 选型 | 锁定版本 |
|---|---|---|
| Python | CPython | 3.13(uv 管理) |
| Node | LTS | 22(pnpm 管理) |
| 数据库 | PostgreSQL + pgvector | 17 / 0.8 |
| 缓存 | Redis | 8 |

## apps/api — 控制面(持凭证)

| 维度 | 选型 |
|---|---|
| Web | FastAPI + Pydantic v2 |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| 迁移 | Alembic(baseline 制,见 ADR-004) |
| 认证 | 自建:argon2-cffi + PyJWT(RS256)+ authlib(GitHub OAuth),见 ADR-003;联邦登录走连接器架构(ADR-006) |
| SQL 治理 | sqlglot(AST 防火墙 + 方言能力储备) |
| 限流 | 按需(nginx limit_req 起步,不预设库) |
| 可观测 | structlog(JSON)+ prometheus-client + OpenTelemetry(按需启用) |

## apps/agent — Agent 面(零凭证)

| 维度 | 选型 |
|---|---|
| 编排 | LangGraph |
| LLM SDK | openai 兼容客户端(通义/DeepSeek 可切) |
| 服务 | FastAPI(仅内网端口,内部令牌认证) |
| 纪律 | 进程内禁止任何 DB 凭证(ADR-002),import-linter 守护 |

## apps/web — React SPA

| 维度 | 选型 |
|---|---|
| 框架 | React 19 + TypeScript(strict) |
| 构建 | Vite(纯 SPA,无 SSR——ADR-001) |
| 样式 | Tailwind CSS v4 |
| 组件 | shadcn/ui(Radix 无头 + 代码自有;Radix 统一走 `radix-ui` 聚合包导入,006 起单轨) |
| 动画 | tw-animate-css(shadcn Tailwind v4 标准配套,sheet/dropdown 等开合动画依赖) |
| 数据 | TanStack Query(服务端状态)+ Zustand(客户端状态) |
| 路由 | React Router v7 |
| 图表 | ECharts(echarts-for-react) |
| 品牌图标 | simple-icons(联邦 logo 等多品牌场景,ADR-006;单个图标可内联官方 SVG) |
| 契约 | openapi-typescript 生成,禁手写 API 类型(ADR-005) |

## 前端样式铁律(继承旧项目 ADR-006/007 纪律,按新栈重写)

1. **组件优先,禁手搓基础交互组件**:按钮/输入/下拉/弹窗/日期/开关等一律用
   shadcn/ui(Radix 无头,代码自有于 `src/components/ui/`);手写仅限**业务组合层**
   (`src/components/` 下的卡片、布局块,由基础组件拼装)。判据:一个"按钮"若需要
   focus 态/键盘可达性/aria 属性,就不该手写。
2. **Tailwind 原子类是唯一样式语言**:不新建 `.css` 文件(`src/index.css` 的
   `@theme` 定义与必要第三方覆盖除外);禁内联 `style=`(第三方图表尺寸等动态值除外);
   禁 `@apply` 堆砌二次封装。
3. **设计 token 单源**:品牌色/字体/圆角只定义在 `index.css` 的 `@theme`,组件经
   token 类引用(`bg-brand-600`);**禁散落硬编码色值**(hex/rgb 直接写进类名)。
   这是旧项目"设计 Token 三方镜像腐化"教训的单源化修正。
4. **图标用 lucide-react**(shadcn 生态标配),禁 emoji 充图标。
5. **亮暗主题**统一走 Tailwind v4 `dark` variant + CSS 变量,色值经 token,
   不在组件里写死主题分支。
6. **图表主题与 token 桥接**:ECharts 不吃 Tailwind 类,其主题色板/字体 MUST
   从 `@theme` 的 CSS 变量派生(运行时读取注入 chart theme),禁止在图表配置里
   硬编码色值——否则图表色与界面色漂移(旧项目"三方镜像"教训的根治条款)。
7. **表现层豁免(审美上限不设限)**:铁律只约束基础交互组件与日常样式;品牌时刻
   (登录页渐变/空状态/加载动效/图表视觉)鼓励自由发挥,约束仅一条——用色仍从
   token 派生。纪律的目的是让这些"审美时刻"稀疏而醒目,不是消灭它们。
8. 过渡条款:shadcn/ui 在主布局域开工时初始化(避免骨架期空转);
   此前**新增页面不得再引入新的手搓基础组件**(现有 Login 页的原生元素届时一并替换)。

## 工程纪律

- Python:ruff(lint+format)+ mypy strict + pytest;uv 锁定
- Web:eslint + prettier + vitest;pnpm 锁定
- 提交前:`make check` 全绿
- CI 四门禁:api 测试 / agent 测试 / web 构建lint / 契约漂移 diff
