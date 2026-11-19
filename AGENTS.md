# Kongming 工作区指引

全能 AI Agent 助手(数据问答与分析为首发王牌)。Python 双进程后端 + React SPA。
品牌基调见 docs/brand.md(色彩 token 单源 index.css)。
**动手前必读**:敏感区改动前先读对应文档(见文末"前置阅读"),不要凭直觉改架构。

## 目录

```text
apps/api/     控制面:FastAPI,持全部凭证(RBAC/租户/SQL 防火墙/审计/Alembic)
apps/agent/   Agent 面:LangGraph,零凭证,SQL 执行 100% 回调 api /internal/*
apps/web/     React 19 SPA:Vite + Tailwind v4;API 类型由生成获得,禁手写
infra/        docker-compose(pgvector/pg17 + redis + api + agent)、Dockerfile、pg-init
contracts/    openapi.json(入库,CI 漂移门禁)
docs/adr/     架构决策记录(为什么)
openspec/     spec-driven 工作流:config.yaml 硬约束 + constitution.md 宪法 + specs/ 行为契约
```

## 常用命令

```bash
make check          # 全量门禁:静态+测试+契约+死代码+重复+依赖+配置 lint(yaml/md/toml/workflow)
make contracts      # 导出 OpenAPI → 生成前端类型;改了 api 路由/模型后必跑并提交产物
# 本地 pg/redis 用统一环境栈 ~/workspace/env(5432/6379,见 docs/HANDOFF.md);
# make up/down 仅服务容器部署形态,本地开发勿自起(与 env 栈抢 5432)
# apps/api:  uv sync && uv run alembic upgrade head && uv run uvicorn kongming.main:app --port 8000
# apps/agent: uv sync && uv run uvicorn kongming_agent.main:app --port 8001
# apps/web:  pnpm install && pnpm dev   (typecheck= tsc -b;lint= eslint;test= vitest run 纯函数)
```

## 架构铁律(违反即返工)

1. **信任边界(ADR-002,宪法 Article IV)**:apps/agent 不得出现任何数据库配置、
   凭证环境变量或 DB 驱动 import(pyproject import-linter 已禁 asyncpg/sqlalchemy/redis)。
   合并 api/agent 进程、给 agent 镜像注入 DB 变量,均属禁止项。
2. **域包分层**:新能力域按 `router(适配)→ service(用例)→ models(领域)→ repository(基础设施)`
   组织;领域代码(models/service 内规则)零框架依赖——不 import fastapi/sqlalchemy/redis,
   持久化用 `typing.Protocol` 接口反转。
3. **契约(ADR-005)**:web 与 agent 的类型一律生成(`pnpm contracts:gen`),手写 API 类型违规;
   `contracts/openapi.json` 必须与代码同步提交。
4. **Schema(ADR-004)**:新表/改表走新的 Alembic 迁移;已规划能力(有 spec)的列一次建全。
   约定:UUID v7 主键、状态列 VARCHAR + CHECK(禁 SMALLINT 魔数/PG ENUM)、
   timestamptz、软删仅 identity 域。
5. **技术栈锁定(docs/tech-stack.md)**:换库/升 major 需先写 ADR。不引入:
   Keycloak、Next.js/SSR、微服务中间件、消息队列、Litestar。
6. **前端样式铁律(docs/tech-stack.md「前端样式铁律」节)**:基础交互组件一律
   shadcn/ui 禁手搓;Tailwind 原子类为唯一样式语言(禁新建 css/内联 style);
   色值/圆角/字体只经 `@theme` 设计 token 引用,禁硬编码;图标用 lucide-react 禁 emoji;
   图表主题色板从 token 派生;品牌表现层(渐变/空态/动效)豁免但用色仍走 token。

## 工作流与约定

- 功能开发走 openspec 流程(`/opsx:propose` → apply → archive),提案必须有 Non-goals;
  涉信任边界/认证/schema/契约的变更须引用对应 ADR。
- 安全规则类(SQL 防火墙/RBAC/租户)测试先行:先写失败测试再实现。
- 文档、ADR、代码注释一律**简体中文**。
- **方法注释**:默认给每个方法写一句简短说明,讲清它负责什么或产生什么结果。只有结合方法名、
  签名和调用场景已足以理解用途,且没有额外业务规则、边界或副作用时,才可省略;不要按方法名
  分类机械省略,也不要只换词复述方法名。
- **其他源码注释只写有用的内容**:默认不加注释;只补充代码看不出的原因、业务含义、边界或
  特殊限制。不要复述语法;对难理解的值、条件或边界,说明它的实际含义和原因。先查代码和项目
  资料,确认不了时不要猜。用准确、平实的简体中文;
  必要且准确的技术术语可以保留。代码注释禁用「综上所述、简言之、本质上、底层逻辑、核心思路、
  该模块旨在、赋能、链路、闭环、下沉、调度体系、基础设施、原子能力」;也避免其他黑话、
  模板套话和虚泛表述。不要写 change 编号或「本次/之前」等过程记录;引用稳定规范或 ADR 时,
  说明它对应的约束。代码行为改变后,同步更新或删除失效注释。
- `uv.lock` 与 `pnpm-lock.yaml` 必须入库(锁文件不进 .gitignore)。
- 仓库已 git 版本化(main 分支);git 写操作(add/commit/push 等)仍需用户逐次明确
  指令,只读查询(status/diff/log)允许。文档不写提交哈希、不谈提交次数/形态——
  git 层细节以 git log 为唯一事实源;纯决策/文档类小变更攒批随功能提交搭车,不单独成提交。
- **接手文档(docs/HANDOFF.md)是项目级事实库,不是执行日记**:只写下一棒
  需要的内容——常见坑、明显缺口、未完成功能、可复现配方、当前口径与现值;
  禁止记「已提交/未提交/本批/本轮/上次推送」等过程叙事与提交状态(工作区与
  历史以 `git status`/`git log` 为唯一事实源),过程细节由提交信息与
  openspec 归档工件承载。写完自查:每句删掉后下一棒会缺信息的才配留下。
- `.env` 从 `.env.example` 复制;compose 对关键变量 `${VAR:?}` 强校验,缺失拒启是刻意的。

## 上游参考项目

前身:`/Users/solvela/nl2sql-data-platform`(Java COLA 双栈,38 变更验证过;全栈容器
可能仍在运行——**绝不触碰其容器/卷/网络**)。

| 场景         | 可参考                                                  | 不可照抄                               |
| ------------ | ------------------------------------------------------- | -------------------------------------- |
| RAG 三路召回 | 召回策略、RRF 融合、缓存失效逻辑                        | Java 实现、双语言 HTTP 回调链          |
| SQL 防火墙   | 规则集与拦截场景(评估用例尤佳)                          | AST 库差异(JSqlParser→sqlglot)         |
| 评估体系     | eval yaml 结构、Execution Match 匹配算法、gold 编写规范 | 目录结构                               |
| Agent 链路   | LangGraph 8 节点流程、prompt 设计、selfheal 分类        | 回调方式                               |
| 反馈闭环     | 状态机语义、精修闭环流程                                | schema 旧约定(雪花 ID/SMALLINT/Flyway) |

纪律:参考**逻辑**不复制**代码**(禁止 import 上游);行为冲突时以 Kongming 的
specs/baseline 为准;上游 specs 取材指引见 docs/roadmap.md。

## 前置阅读(按改动区域)

| 改动区域                  | 先读                                                                    |
| ------------------------- | ----------------------------------------------------------------------- |
| 战略/方向规划、新能力立项 | docs/adr/010(AI 五大方向定调)+ docs/roadmap.md 第四梯队                 |
| 认证/登录/用户            | docs/adr/003(自建认证,不用 Keycloak)+ 006(联邦连接器)                   |
| api↔agent 交互、部署单元  | docs/adr/002(信任边界)                                                  |
| 数据库表结构              | docs/adr/004 + apps/api/alembic/versions/0001_baseline.py(演进走新迁移) |
| 前后端契约/加接口         | docs/adr/005 + make contracts                                           |
| 任何"要不要引入 X"        | docs/adr/001(含否决清单)+ openspec/constitution.md                      |

## 前端宪法(隆中聚合层单源)

web 前端统一遵循聚合目录 `../FRONTEND-CONSTITUTION.md`(Article 0–10);本节为执行摘要,冲突时以宪法为准。

- 元原则:**用组件不用手搓;用默认不胡乱覆盖;用 token 不用魔法值。**
- 色彩:只引用 index.css 语义 token(含 success/warning),禁 `#hex`/Tailwind 字面色板;
  图表取色走 `lib/utils.ts` 的 `cssVar()`。暗色是一级公民:**新增 token 必须同时给 `.dark` 值**,
  组件禁新增 `dark:` 硬编码色(Article 3/10)。
- 组件:基础交互件一律 `components/ui/`,业务代码禁裸 `<button>/<input>/<select>/<textarea>`;
  破坏性操作必须 AlertDialog 确认(禁 window.confirm);≥2 处同类 UI 必须抽公共组件;
  同一覆盖 ≥3 处收编进组件默认值(Article 1/2)。
- 页面骨架只用标准件:PageHeader / FormRow / FieldLabel / Pagination / StatCard / StatusBadge /
  ErrorAlert / EmptyState,禁每页手搓骨架;间距只用 Tailwind 刻度,禁任意值间距与
  inline style 的 margin/padding/width(Article 4/6)。
- 响应式(移动优先,mobile-experience 域):无前缀基线即窄屏形态,`md:` 单一
  折叠点恢复桌面;壳层窄屏 = BottomNav + 管理层进(/admin 索引页)+ 会话抽屉
  (入口在对话区页头),不复刻侧栏;结构化列表一律 `DataView` 双形态标准件
  (md+ 表格 / md- 卡片,行操作窄屏收溢出菜单),新建/编辑对话框窄屏全屏承载
  (ui/dialog 基类单点收编;AlertDialog 窄屏贴底抽屉、sm+ 居中——2026-10-02 三仓对齐
  木牛登记形态);问答结果表等动态列
  豁免卡片化,保留卡片内横滚;贴边区消费安全区工具类(pb-safe 等),验收档 =
  375(主)/768/1280 三档 + 亮暗两态(Article 9)。
  备注(设计 D7):聚合目录 `../FRONTEND-CONSTITUTION.md` 与
  `../scripts/sync-frontend-standard.sh` 均在原位,同步机制照下条执行;
  本变更新增的共享组件(data-view/empty-state/error-alert/bottom-nav 等)
  尚未收录进双仓标准件清单,是否双仓收编待 owner 裁定,先行修改源=本仓。
- ui/ 与共享组件是双仓标准件(与 jinguan 逐字节一致;theme-store 例外:同构不同 key):
  **先改本仓再同步** → 聚合目录 `bash ../scripts/sync-frontend-standard.sh`。
- 收尾门禁:`pnpm typecheck && pnpm lint` + 宪法 Article 8 自检 grep + 亮暗两态走查改动页。
