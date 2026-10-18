# Proposal: stack-refresh-2026-10(全栈升级至最新稳定版)

## Why

全栈版本盘点(`docs/stack-refresh-2026-10.md`,2026-10-18 registry 实查)实证
两类欠账:①**下限漂移**——pyproject/pnpm 版本下限远低于实锁与最新稳定版
(langgraph 下限 0.2.60 实锁 1.2.11、openai 下限 1.66 实锁 3.1.0),新环境
解析存在回退旧版风险;②**major 落后**——Vite 6→8(Rolldown 已是默认)、
SQLAlchemy 2.0→2.1、echarts 5→6、vitest 3→5、eslint 9→10、react-router
7→8(v6/Remix v2 已 EOL,v7 仅安全维护),外加 React Compiler 1.0 已稳定
(vite-plugin-react 原生开关,web 有 48 处手写 memo 的真实替代面)。MCP SDK
收编另立 `adopt-mcp-sdk2` 变更,不在此处夹带。

## What Changes

- **后端常规刷新**:fastapi/alembic/pydantic/cryptography/authlib/uvicorn/
  sqlglot/langgraph(patch 线内)等锁跟进至最新稳定;**下限对齐实锁**
  (langgraph>=1.2、openai>=3、fastapi>=0.143 等,消除下限漂移)。
- **后端 major**:SQLAlchemy 2.0→2.1(greenlet 经 `[asyncio]` extra 继续
  显式依赖);openai 3.1→3.35(3.0 起默认 HTTPX2,本项目已显式依赖 httpx,
  无感);anthropic 1.8→1.15(滞留 7 个 minor)。
- **前端 major**:Vite 6→8 + @vitejs/plugin-react 4→6(Rolldown 构建);
  vitest 3→5(node>=22.12 基线);eslint 9→10(typescript-eslint 8.71 支持面
  内);react-router 7→8(ESM-only,低破坏);react 19.2→19.3;echarts 5→6
  (新默认主题+原生暗色,token 派生主题覆盖后视觉回归走查)。
- **React Compiler 启用**:vite-plugin-react 编译器开关打开(自动 memoization,
  构建期优化零行为变化);既有 48 处手写 memo/useCallback 保留不删(叠加生效,
  后续新代码逐步免写)。
- **基础设施**:compose `pgvector/pgvector:pg17` → `pg18`(开发机已运行
  pg18,部署形态对齐;AIO 扫描提速与原生 uuidv7() 顺带可用);Python 运行时
  3.13 → 3.14(Dockerfile 与 requires-python;ctranslate2 已有 3.14 轮子,
  可选档——任一依赖失配即单项回退 3.13)。
- **治理文档随行**:docs/tech-stack.md 与 openspec/config.yaml context 的
  版本表述同步(PG18/Python 3.14/Vite 8 等)。

## Capabilities

### New Capabilities

(无。)

### Modified Capabilities

(无——本变更为行为保持的栈刷新,所有 spec 行为契约不变;
`skip_specs: true`。图表默认视觉随 echarts 6 有微差,但 web 契约不钉像素
视觉,亮暗两态走查作为验收而非 spec 变更。)

## Impact

- `apps/api`+`apps/agent`:pyproject 下限对齐、uv.lock 刷新、SQLAlchemy 2.1
  迁移面(typing/弃用 API 按官方 migration_21 对照)。
- `apps/web`:package.json majors、vite/vitest/eslint 配置迁移(Rolldown
  配置键静默破坏面按迁移指南核对)、React Compiler 开关。
- `infra`:compose 镜像 pg18;`apps/*` Dockerfile python3.14 基镜像。
- 工具链:node>=22.12 基线(mise 治理面)。
- 契约零变化(`make contracts` 产物 diff 为空预期)。

## Non-goals

- **TypeScript 7(tsgo)**:typescript-eslint 尚不兼容(官方 tracking
  #10940,预期 7.1 生态跟上),现有「tsc 钉 5.x」纪律维持,登记 ADR-011
  暂缓项。
- **PostgreSQL 19**:RC1 刚过(2026-10),非稳定成熟档;目标 18。
- **Python 3.15**:刚发布,等一个补丁周期。
- **MCP SDK 收编**:归 `adopt-mcp-sdk2` 变更(含 mcp 依赖窗调整)。
- **手写代码大规模重构**(memo 清理/uuid7.py 替换等):uuid7.py 为 19 行
  纯函数有测试,不换;memo 保留叠加(ADR-011 裁决)。
- **Tailwind/TanStack/zustand/xyflow/openapi-typescript**:已在最新稳定
  线内,无 major 可升。
