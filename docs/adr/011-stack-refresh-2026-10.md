# ADR-011: 2026-10 全栈刷新裁决(最新稳定版 + 手搓替代)

日期: 2026-10-18

状态: 已接受(架构裁决已委托;实施载体=openspec 变更 `stack-refresh-2026-10`
与 `adopt-mcp-sdk2`,行为保持项+协议收编项分立)

## 背景

全栈版本对照调研(`docs/stack-refresh-2026-10.md`,registry 实查)发现两类
欠账:版本下限漂移(langgraph 下限 0.2.60 vs 实锁 1.2.11)与 major 落后
(Vite 6→8、SQLAlchemy 2.0→2.1、echarts 5→6、vitest 3→5、eslint 9→10、
react-router 7→8)。同时确认三处「框架原生能力可替代手搓」的机会:MCP 协议
栈(官方 SDK 2.x 原生支持 2026-07-28 无状态规范修订,替代双向 689 行手写
JSON-RPC)、React Compiler 1.0(自动 memoization,替代手写 memo 的增量)、
PG18 原生 uuidv7()(评估后不采纳,见否决项)。技术栈锁定纪律要求升 major
先立 ADR,本 ADR 为单源。

## 决策

### 1. 采纳的 major 升级(载体:`stack-refresh-2026-10`)

| 项 | 升级 | 依据 |
|---|---|---|
| SQLAlchemy | 2.0 → 2.1 | typing 泛型 Table/性能;`[asyncio]` extra 继续,
greenlet 显式;PG 驱动不变(asyncpg) |
| openai SDK | 3.1 → 3.35 | 3.0 起 HTTPX2 默认(本项目已显式依赖 httpx,
无感);responses API 客户端面持续演进 |
| Vite + plugin-react | 6 → 8 / 4 → 6 | Rolldown 默认(10-30x 构建提速
量级);React Compiler 的原生开关也要求 plugin-react 6 |
| vitest | 3 → 5 | 性能线;node>=22.12 基线(4 为过渡,一步到位 5) |
| eslint | 9 → 10 | flat config 已是现状;typescript-eslint 8.71 支持
面内;peer 冲突则单项回退条款内建 |
| react-router | 7 → 8 | v6/Remix v2 已 EOL、v7 仅安全维护;v8 官方定性
「刻意无破坏」,SPA library 用法迁移面小 |
| react | 19.2 → 19.3 | minor 线内 |
| echarts | 5 → 6 | 新默认主题/原生暗色/动态主题切换;本项目 token
派生主题覆盖主要视觉面,亮暗走查为验收 |
| PostgreSQL(compose) | 17 → 18 | 开发机已运行 pg18(2026-10-11 localenv
收敛),部署形态对齐;AIO 扫描提速顺带 |
| Python 运行时 | 3.13 → 3.14 | ctranslate2 已发 3.14 轮子(CTranslate2
#1926);可选档,任一依赖失配整批回退 |

### 2. 采纳的手搓替代

- **MCP 协议栈收编官方 SDK 2.x**(载体:`adopt-mcp-sdk2`,独立变更):
  出站 519 行/入站 170 行手写 JSON-RPC 替换为官方栈。决定性依据:①MCP 规范
  2026-07-28 修订无状态化(会话握手废除),手写的 113 会话透传维护的是被
  规范废弃的概念;②手搓动机「零新依赖」(028 design D1)已失效——mcp SDK
  早是 dev 依赖(test_mcp_live.py 用 FastMCP 起真 server);③协议货币化
  (当前修订声明+跨修订兼容)由 SDK 原生承载,自研无此跟进能力。
- **React Compiler 1.0 启用**(载体:`stack-refresh-2026-10` 批③):
  vite-plugin-react 原生开关,构建期自动 memoization;web 现有 48 处手写
  memo/useCallback 保留叠加(不删),后续新代码逐步免写。

### 3. 否决/暂缓项

| 项 | 裁决 | 理由 |
|---|---|---|
| TypeScript 7(tsgo) | 暂缓 | 10x 类型检查诱人,但 typescript-eslint
不兼容(官方 tracking #10940,预期 7.1 世代跟上);lint 门禁是质量闸,
不能并行双 TS 版本;既有「tsc 钉 5.x」纪律维持,生态就绪后另立变更 |
| PostgreSQL 19 | 否决(本次) | RC1 刚过(2026-10-15 前后),非稳定
成熟档;目标 18 |
| Python 3.15 | 否决(本次) | 刚发布,等一个补丁周期 |
| uuid7.py 换库/换 PG18 原生 | 否决 | 19 行纯函数、有测试、零依赖;
PG18 uuidv7() 仅适用 DB 侧 DEFAULT 场景,本项目 ORM 侧生成主键(插入前
即需 ID),替换为负收益 |
| LangGraph 检查器换官方 PostgresSaver | 否决(既有裁决重申) | 违反
ADR-002(agent 零凭证);add-graph-checkpoint 已按 /internal 回调适配器
设计,不受本刷新影响 |
| MCP SDK 高级特性(resumability/OAuth) | 否决(本次) | 无真实 server
场景;收编只求协议栈等价替换 |

### 4. 下限对齐原则(新增纪律)

依赖声明下限 MUST 与实锁 major 线一致(关键运行时依赖加窗式上限),
防止新环境重解析回退旧 major——langgraph 0.2 下限是实证反面教材。

## 与既有决策的关系

- **ADR-001/tech-stack 锁定**:本 ADR 即「升 major 先写 ADR」流程的执行。
- **ADR-002**:MCP 收编全部发生在 api 控制面,信任边界零改动;
  LangGraph 官方检查器否决重申该边界。
- **ADR-008(多协议出站)**:openai/anthropic SDK 升级在该框架内,协议
  集成测试回归为验收。
- **ADR-004(schema)**:PG18 升级不涉 schema 约定变化;uuidv7 主键约定
  不变(生成侧不动)。
- **ai-platform 对齐调研**:「LangGraph 生态 1.x 稳定线」论断被实锁证实
  (1.2.11),运行时裁决不受影响。

## 后果

- 正面:协议货币化(MCP 新规范)、构建链现代化(Rolldown/Compiler)、
  下限漂移欠账清偿、部署与开发环境版本对齐(pg18)。
- 负面:四批升级的门禁成本(每批全量 make check);eslint 10 与
  Python 3.14 两处不确定性以回退条款内建;React Compiler 对存量组件的
  告警清单需登记消化(不阻断)。
