# 001 Design

本变更为奠基性变更,全部关键决策已沉淀为 ADR(本文件只做索引与补充过程记录):

| 决策 | ADR | 一句话 |
|---|---|---|
| 技术栈 | 001 | Python 3.13 双进程 + React 19 SPA(55/45 定案,含否决清单) |
| 信任边界 | 002 | 进程边界是构造级保证,import-linter 只是纵深一层 |
| 认证 | 003 | 弃 Keycloak 自建,登录页即前端路由(替代 459 行 PatternFly 镜像) |
| Schema | 004 | Alembic 单 baseline,前系统 15/50 补丁链的最终态整合 |
| 契约 | 005 | OpenAPI 生成 + CI 漂移门禁(平移前系统 038 已验证模式) |

## 过程记录(对评审循环的诚实存档)

1. **选型经三轮对抗评审收敛**:初版方案被外部评审指出信任边界降级为 lint 的
   实质错误、python-jose CVE、sqlglot 多方言论据被 PG-only 事实出局等;
   终局裁定(双向修正):现有系统不重写;新项目默认第一选型 = 本方案。
2. **平移规则**:行为契约保留、实现引用剥离(Java/COLA/Keycloak/Flyway 全剔),
   `data_scope=1/5` 魔数 → `ALL/DEPT/SELF` 等字符串枚举,反馈状态机对齐
   `NONE→PENDING→ADOPTED/REJECTED/DISMISSED`,审计 `status` → `verdict(PASS/BLOCKED/ERROR)`。
3. **首次跑通暴露 10 个真实问题并全部修复**(asyncpg 多语句、middleware 抛
   HTTPException 变 500、pg-init 时序、pnpm 11 构建脚本拦截、端口三连撞等)。
4. **全覆盖评审修复 7 项缺陷**(password.hash 调用不存在的函数、Agent 图
   retry 绕过 selfheal 的无限循环、ADR-002 import-linter 未生效、Dockerfile
   缺 uv.lock 等),剔除 2 类虚构问题(sqlglot 类名、S105 误报)。
