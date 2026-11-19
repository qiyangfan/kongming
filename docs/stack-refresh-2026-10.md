# 2026-10 全栈版本对照与升级裁决

> **本文定位**:回答三个问题——当前栈离最新稳定版多远、哪些 major 值得升、
> 哪些框架原生功能可以替代项目里的手搓实现。数据源=PyPI/npm registry 实查
> (2026-10-18)+ 官方发布说明调研;裁决单源=ADR-011,实施载体=openspec 变更
> `stack-refresh-2026-10`(行为保持)与 `adopt-mcp-sdk2`(协议收编)。
>
> **终态(2026-10-18 apply 完成后勾注)**:四批全部落地——后端常规+下限对齐
> (批①)/SA 2.1+openai 3.35+anthropic 1.15(批②)/Vite8+vitest5+eslint10+RR8+
> echarts6+React Compiler 1.0(批③;echarts-for-react 切 esm/ 导入修 Vite8
> CJS 互操作)/pg18 compose 对齐+Python 3.14 容器镜像(批④,容器 healthz smoke
> 过);TypeScript 7 维持暂缓。构建耗时对比:Vite8+Rolldown 全量 build 577ms
> (Vite6 时代 ~10s 级);React Compiler 启用后 466ms、编译器告警清单=零条
> (手写 memo 保留叠加),页面 chunk 因内联 memo 微涨(vendor 恒定)。
>
> **一句话结论:栈整体新鲜(多数依赖实锁在最新 minor),真正的大项是
> ①MCP 规范 2026-07-28 无状态修订使双向手写协议栈过时→收编官方 SDK 2;
> ②前端构建链跨两代(Vite 6→8 Rolldown + React Compiler 1.0 替代手写 memo);
> ③四处否决/暂缓(TS7 等 typescript-eslint、PG19、Python 3.15、uuid7 换库)。**

---

## 1. 后端依赖对照(PyPI,2026-10-18)

| 依赖                                 | 实锁                    | 最新                   | 差级         | 裁决                                                              |
| ------------------------------------ | ----------------------- | ---------------------- | ------------ | ----------------------------------------------------------------- |
| fastapi                              | 0.141.1                 | 0.143.3                | minor        | 升,下限对齐                                                       |
| uvicorn                              | 0.52.3                  | 0.54.0                 | minor        | 升                                                                |
| pydantic                             | 2.13.4                  | 2.14.1                 | minor        | 升                                                                |
| **sqlalchemy**                       | 2.0.52                  | **2.1.5**              | **minor 线** | **升(ADR-011 §1:typing/性能;[asyncio] extra 保 greenlet)**        |
| alembic                              | 1.19.1                  | 1.20.0                 | minor        | 升                                                                |
| **openai**                           | 3.1.0                   | 3.35.1                 | 34 minor     | **升(3.0 起 HTTPX2 默认,本项目已显式依赖 httpx 无感;下限提 >=3)** |
| **anthropic**                        | 1.8.0                   | 1.15.0                 | 7 minor      | **升(滞留最久项)**                                                |
| langgraph                            | 1.2.11                  | 1.2.16                 | patch        | 升;**下限从 0.2.60 对齐到 >=1.2,<2(下限漂移反面教材)**            |
| sqlglot                              | 30.17.0                 | 30.23.0                | patch        | 升                                                                |
| redis                                | 8.1.0                   | 8.1.0                  | —            | 已最新                                                            |
| httpx                                | 0.28.1                  | 0.28.1                 | —            | 已最新                                                            |
| cryptography                         | 50.0.0                  | 50.0.2                 | patch        | 升                                                                |
| authlib                              | 1.7.2                   | 1.9.1                  | minor        | 升                                                                |
| pyjwt / argon2-cffi / faster-whisper | 2.13.0 / 25.1.0 / 1.2.1 | 同                     | —            | 已最新                                                            |
| **mcp(dev)**                         | 1.30.0                  | **2.3.0**              | **major**    | **收编官方 SDK 2(独立变更 adopt-mcp-sdk2;dev→prod,<2→>=2.3,<3)**  |
| pytest / ruff / mypy                 | 9.1.1 / 0.16.3 / 2.3.1  | 9.1.1 / 0.17.1 / 2.4.0 | 工具线       | 升                                                                |
| pyright                              | 1.1.411                 | 1.1.414                | patch        | 升                                                                |

## 2. 前端依赖对照(npm,2026-10-18)

| 依赖                                         | 实锁                      | 最新             | 差级        | 裁决                                                                                  |
| -------------------------------------------- | ------------------------- | ---------------- | ----------- | ------------------------------------------------------------------------------------- |
| react / react-dom                            | 19.2.8                    | 19.3.1           | minor       | 升                                                                                    |
| **vite**                                     | 6.4.3                     | **8.3.4**        | **2 major** | **升(Rolldown 默认,10-30x 构建提速量级;迁移指南点名 3 个静默破坏配置键)**             |
| @vitejs/plugin-react                         | 4.7.0                     | 6.1.2            | 2 major     | 随 Vite 8 升;React Compiler 原生开关入口                                              |
| **vitest**                                   | 3.2.7                     | **5.0.5**        | **2 major** | **升(node>=22.12 基线先行)**                                                          |
| **eslint**                                   | 9.39.5                    | **10.13.0**      | **major**   | **升(typescript-eslint 8.71 支持面;peer 冲突单项回退条款)**                           |
| **react-router**                             | 7.18.2                    | **8.4.0**        | **major**   | **升(v6/Remix v2 已 EOL、v7 仅安全维护;v8 官方定性低破坏,ESM-only)**                  |
| **echarts**(echarts-for-react 3.0.6)         | 5.6.0                     | **6.1.0**        | **major**   | **升(新默认主题/原生暗色/动态主题切换;本项目 token 派生主题覆盖主视觉,亮暗走查验收)** |
| typescript                                   | 5.9.3                     | 7.0.2            | major       | **暂缓(tsgo 10x 但 typescript-eslint 不兼容,#10940;等 7.1 世代)**                     |
| tailwindcss                                  | 4.3.3                     | 4.3.3            | —           | 已最新                                                                                |
| @tanstack/react-query                        | 5.101.4                   | 5.104.4          | minor       | 升                                                                                    |
| zustand / @xyflow/react / openapi-typescript | 5.0.15 / 12.12.0 / 7.13.0 | 同               | —           | 已最新                                                                                |
| lucide-react / radix-ui                      | 1.31.0 / 1.6.7            | 1.59.0 / 1.8.1   | minor       | 升                                                                                    |
| prettier / stylelint                         | 3.9.6 / 17.15.0           | 3.9.10 / 17.16.0 | patch       | 升                                                                                    |

## 3. 基础设施与运行时

| 项                  | 现状                      | 最新稳定            | 裁决                                                                                                                |
| ------------------- | ------------------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL(compose) | pgvector/pg17             | **18**(19 RC1 刚过) | **升 18**——开发机 localenv 已运行 pg18(2026-10-11 收敛),部署形态对齐;AIO 扫描 2-3x 与原生 uuidv7() 顺带可用;19 不追 |
| pgvector            | (随镜像)                  | 0.8 线              | 随 pg18 镜像                                                                                                        |
| Redis               | 8-alpine                  | 8.x                 | 已最新                                                                                                              |
| Python 运行时       | 3.13(Dockerfile/requires) | 3.14(3.15 刚发)     | **升 3.14 可选档**(ctranslate2 已发 3.14 轮子,CTranslate2 #1926);3.15 等补丁周期;任一依赖失配整批回退 3.13          |
| node                | (mise 治理)               | ≥22.12 需求         | vitest5/compiler 基线先行钉                                                                                         |

## 4. 手搓点排查:框架原生 vs 自研(本调研核心问)

| 手搓点                         | 现状                                                                         | 原生替代                                                                                                | 裁决                                                                                                                                                  |
| ------------------------------ | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **MCP 出站客户端**             | `mcp/client.py` 519 行手写 JSON-RPC over Streamable HTTP(028;113 补会话透传) | 官方 SDK 2.x client session:**2026-07-28 规范修订无状态化(会话握手废除),SDK 原生支持新修订+兼容旧修订** | **收编**(adopt-mcp-sdk2)。手搓动机「零新依赖」已失效——mcp SDK 早是 dev 依赖(test_mcp_live.py 用 FastMCP 起真 server);会话透传维护的是被规范废弃的概念 |
| **MCP 入站端点**               | `mcp/inbound.py` 170 行手写方法分发,协议修订钉 2025-03-26                    | 官方 SDK 2.x server(FastAPI 挂载)                                                                       | **收编**(同上):声明当前修订,旧客户端 SDK 兼容层透明支持;api-key/闸/隔离语义逐字保留                                                                   |
| **手写 memoization**           | web 48 处 useMemo/useCallback                                                | **React Compiler 1.0(2025-10 稳定)**:构建期自动 memo,vite-plugin-react 原生开关                         | **启用编译器,手写保留叠加**(不删——编译器与手写共存,新代码逐步免写)                                                                                    |
| uuid7.py                       | 19 行纯函数(RFC 9562)                                                        | PG18 原生 uuidv7() / uuid-utils 库                                                                      | **不换**:ORM 侧生成主键(插入前需 ID),DB 侧 DEFAULT 不适配;19 行有测试零依赖,替换为负收益                                                              |
| LangGraph 检查器               | (在途)add-graph-checkpoint 自定义回调适配器                                  | 官方 AsyncPostgresSaver                                                                                 | **不换**(既有裁决重申):官方 Saver 要求 agent 直连 PG,违反 ADR-002 零凭证                                                                              |
| SSE 中继/工具网关/SQL 防火墙等 | 域核心资产                                                                   | 无对应原生形态                                                                                          | 不在替代讨论面(架构差异化所在)                                                                                                                        |

## 5. 来源

- PyPI/npm registry 实查(2026-10-18,版本号以当日 dist-tags/latest 为准);
- MCP Python SDK 2.0:[releases](https://github.com/modelcontextprotocol/python-sdk/releases)、
  [What's new in v2](https://py.sdk.modelcontextprotocol.io/whats-new)、
  [迁移指南](https://py.sdk.modelcontextprotocol.io/migration)、
  [无状态规范分析](https://dev.to/krlz/mcp-went-stateless-what-the-2026-07-28-spec-actually-changes-273k);
- SQLAlchemy 2.1:[What's New](http://docs.sqlalchemy.org/en/latest/changelog/migration_21.html)、
  [发布公告](https://www.sqlalchemy.org/blog/2026/09/24/sqlalchemy-2-1-0-released);
- openai-python 3.x:[CHANGELOG](https://github.com/openai/openai-python/blob/main/CHANGELOG.md);
- Vite 8:[迁移指南](https://vite.dev/guide/migration);Vitest 5:[博客](https://vitest.dev/blog/vitest-5.html);
- React Router v8:[发布公告](https://remix.run/blog/react-router-v8);
- React Compiler 1.0:[官方公告](https://react.dev/blog/2025/10/07/react-compiler-1);
- ECharts 6:[升级指南](https://echarts.apache.org/handbook/en/basics/release-note/v6-upgrade-guide);
- PostgreSQL 18:[发布公告](https://www.postgresql.org/about/news/postgresql-18-released-3142);
- TypeScript 7:[发布公告](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0)、
  [typescript-eslint #10940](https://github.com/typescript-eslint/typescript-eslint/issues/10940);
- CTranslate2 Python 3.14 支持:[releases](https://github.com/OpenNMT/CTranslate2/releases)。
