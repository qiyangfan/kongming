# Design

## Context

版本现状与最新稳定版全量对照见 `docs/stack-refresh-2026-10.md`(registry
实查,2026-10-18)。本变更承载其中「行为保持」的全部升级项;MCP SDK 收编
(major 且有行为面)已拆至 `adopt-mcp-sdk2`。技术栈锁定纪律(docs/tech-stack:
换库/升 major 需先写 ADR)由 **ADR-011** 承载裁决,本 design 只定实施方式。

## Goals / Non-Goals

**Goals:**

- 四批升级(major 后端/major 前端/基础设施与运行时/常规刷新+下限对齐)
  各自独立可验收、可单项回退;
- 全程行为保持:契约产物零 diff、全量门禁绿、亮暗两态视觉走查过。

**Non-Goals:**

- 见 proposal Non-goals;另:不借升级顺带改任何业务代码(除编译器开关与
  配置迁移触及的机械适配)。

## Decisions

**D1 分四批、批内可回退,验收门禁逐批过。**
批序=①后端常规+下限对齐(零风险热身,先验证门禁链路)→②后端 major
(SQLAlchemy 2.1/openai/anthropic)→③前端 major+编译器(Vite8/vitest5/
eslint10/RR8/react/echarts6/Compiler)→④基础设施(pg18 镜像/Python 3.14)。
每批一次 `make check`(对应子集)+ 契约 diff 断言;批间独立提交语义,
单项失配回退该批不牵连。

**D2 下限对齐=把 pyproject 下限提到当前实锁 major 线,窗式约束防漂移。**
langgraph>=1.2,<2、openai>=3,<4、fastapi>=0.143 等(关键运行时依赖加上限
窗;工具链类只提下限)。理由:uv.lock 保护当前环境,但新环境重建/依赖新增
触发重解析时,漂移下限可能把旧 major 解析回来(尤其 langgraph 0.2→1 的
API 面差异)。

**D3 SQLAlchemy 2.1 迁移面=弃用 API 对照 + `[asyncio]` extra 显式化。**
2.1 的 greenlet 改 opt-in:继续用 `sqlalchemy[asyncio]` 声明(greenlet 随
extra 进来),不裸改;Table 泛型 typing 与 tstring 为增项,不强制回改存量
代码(mypy strict 下无新增告警即可)。按官方 migration_21 清单逐项 grep
受影响 API(预期面:几乎为零——本项目未用弃用路径)。

**D4 前端构建链一次到位(Vite8+plugin-react6+vitest5+Compiler),node 基线
先行。** mise 治理面先把 node 钉到 >=22.12 LTS 档(编译器与 vitest5 共同
前置);Vite 8 的 Rolldown 配置键静默破坏面(官方迁移指南点名三项)逐项
核对本项目 vite.config;React Compiler 经 plugin-react 的 compiler 选项启用,
先开 `mode: compile` 默认档,构建产物体积与冷启动对比记录进 HANDOFF。
eslint 10:flat config 已是现状,typescript-eslint 升 8.71 对齐 peer;若
peer 冲突则 eslint 停 9.x(单项回退条款)。

**D5 echarts 6 视觉面=token 主题回归,不做像素对齐。**
echarts-for-react 升 3.0.7;主题色板本就从 token 派生(lib/utils.cssVar),
v6 新默认主题仅影响未被 token 覆盖的次要元素(图例位置/轴线风格);
验收=问答图表页/报告图表亮暗两态走查 + 既有图表快照测试(如有)通过,
发现形态劣化按 token 补覆盖,不回退版本。

**D6 pg18 与 Python 3.14 都走「开发机已验证/轮子已确认」路线。**
pg18:开发机 localenv 已运行 pg18+pgvector(memory 记载),本变更只是把
compose 部署形态对齐,无数据迁移(新部署基线;存量部署升级手册不在本变更)。
Python 3.14:ctranslate2 已发 3.14 轮子(CTranslate2 #1926),Dockerfile 基
镜像 `uv:python3.14-bookworm-slim` 存在性 apply 期确认;任一依赖(c 扩展
类)无轮子即整批回退 3.13(下限仍 >=3.13 兼容)。

**D7 治理文档同步单点:tech-stack.md + config.yaml context 一次改齐。**
版本表述(PG18/Python 3.14/Vite 8/vitest 5/eslint 10)在两处单源同步,
避免 spec context 与 tech-stack 漂移;ADR-011 为决策单源。

## Risks / Trade-offs

- [Vite 8 配置键静默破坏(构建不报错但产物异)] → 构建产物 smoke(本地
  `pnpm build` + dev server 起动冒烟)+ 迁移指南点名项逐项核对。
- [SQLAlchemy 2.1 未知弃用面] → migration_21 清单 grep + 全量 pytest
  (async engine 路径全覆盖);mypy strict 零新增。
- [eslint 10 与 typescript-eslint peer 冲突] → D4 单项回退条款(停 9.x,
  登记 ADR-011 附注,下次刷新再试)。
- [Python 3.14 次生轮子缺口] → D6 整批回退条款(回 3.13 无代码影响)。
- [React Compiler 与个别组件 Rules-of-React 违例冲突] → 编译器构建期会
  跳过违例组件并告警(不阻断);告警清单登记 HANDOFF,不顺手重构。
- [echarts 6 视觉回归] → D5 走查门禁。

## Migration Plan

四批依次落地(每批 make check 对应子集绿即并入);全部完成后 `make check`
全量 + `make contracts` 零 diff 断言 + 亮暗两态走查。回滚=批粒度 revert,
不设特性开关(版本无灰度语义)。

## Open Questions

(无——eslint 10 与 Python 3.14 的不确定性已内建回退条款,不阻塞开工。)
