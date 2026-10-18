# Tasks

## 1. 批①:后端常规刷新 + 下限对齐

- [ ] 1.1 pyproject(双 app)下限对齐与窗式约束(design D2 清单:
  langgraph>=1.2,<2、openai>=3,<4、fastapi>=0.143 等);`uv lock --upgrade`
  刷新常规项(fastapi/alembic/pydantic/cryptography/authlib/uvicorn/sqlglot/
  langgraph patch 等);验证:uv sync 成功,diff 中无 major 意外项
- [ ] 1.2 `make check` api+agent 全量(ruff+mypy+pytest)通过;验证:门禁
  绿,契约产物无涉

## 2. 批②:后端 major

- [ ] 2.1 SQLAlchemy 2.0→2.1:按官方 migration_21 清单 grep 受影响 API;
  `[asyncio]` extra 确认 greenlet 在列;验证:mypy strict 零新增告警 +
  pytest 全量绿(异步引擎路径)
- [ ] 2.2 openai 3.1→3.35 与 anthropic 1.8→1.15:多协议出站(ADR-008)
  协议集成测试全量回归(MockTransport 注入形态不变);验证:agent pytest
  协议组绿

## 3. 批③:前端 major + React Compiler

- [ ] 3.1 node 基线钉 >=22.12(mise 治理面);Vite 6→8 + @vitejs/plugin-react
  4→6:vite.config 按迁移指南核对 Rolldown 破坏键;验证:`pnpm build` 产物
  smoke + dev server 起动冒烟,构建耗时对比记录
- [ ] 3.2 vitest 3→5 + eslint 9→10 + typescript-eslint→8.71:配置迁移与
  peer 校验(eslint 冲突即按 D4 单项回退条款);验证:`pnpm test:run` +
  `pnpm lint` 绿
- [ ] 3.3 react 19.3 + react-router 7→8 + echarts 5→6(含 echarts-for-react
  3.0.7):路由与图表回归;验证:图表页/报告页亮暗两态走查(design D5 口径)
- [ ] 3.4 React Compiler 启用(plugin-react compiler 选项,默认档);验证:
  构建告警清单登记 HANDOFF(违例组件跳过不阻断),冷启动/体积对比留档
- [ ] 3.5 `pnpm typecheck && pnpm lint && pnpm test:run` 全绿(tsc 维持 5.x)

## 4. 批④:基础设施与运行时

- [ ] 4.1 compose `pgvector/pgvector:pg17`→`pg18`;验证:compose 起 pg18+
  pgvector 健康,alembic upgrade head 全量迁移通过,问答主链冒烟
- [ ] 4.2 Python 3.13→3.14:双 Dockerfile 基镜像 + requires-python 上限档
  评估(保 >=3.13 兼容声明或提 >=3.14,按 apply 时依赖面定);验证:镜像
  构建 + 容器内 smoke;任一 c 扩展轮子缺失即整批回退 3.13(design D6)

## 5. 收尾门禁

- [ ] 5.1 `make check` 全量 + `make contracts` 零 diff 断言;tech-stack.md
  与 openspec/config.yaml context 版本表述同步(PG18/Python 3.14/Vite 8/
  vitest 5/eslint 10);HANDOFF 补刷新基线(版本表/构建耗时/编译器告警清单)
- [ ] 5.2 docs/stack-refresh-2026-10.md 对照表终态勾注(已升/回退/暂缓),
  与 ADR-011 附注对齐(eslint 10 或 Python 3.14 如触发回退条款,附注记录)

## Workflow follow-up

- 归档走 `/opsx:archive`
- MCP SDK 收编(`adopt-mcp-sdk2`)独立变更,本变更勿动 `mcp` 依赖
- TypeScript 7 暂缓项重估时机:typescript-eslint 官方支持落地后(预期
  TS 7.1 世代),届时另立变更
