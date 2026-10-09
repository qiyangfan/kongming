# 001 Tasks

## 实施

- [x] ADR-001~005 + tech-stack 锁定 + 宪法六条 + config.yaml 硬约束
- [x] apps/api 骨架:配置 fail-closed / 认证三件套(argon2id、PyJWT RS256、GitHub OAuth)/ sqlglot 防火墙 / /internal 受控通道
- [x] apps/agent 骨架:零凭证 settings 自检 + LangGraph 图(重试必经 selfheal 计数)
- [x] apps/web 骨架:Vite + Tailwind v4 + 登录页 + OpenAPI 类型生成脚本
- [x] Alembic baseline 0001(15 表,补丁史最终态整合)
- [x] infra:compose + 双 Dockerfile(携带 uv.lock,frozen)+ pg-init 只读角色
- [x] openspec 15 域 spec 平移改写入库;roadmap 41 域登记
- [x] 首次全链路跑通(暴露并修复 10 问题)
- [x] 全覆盖评审:修复 7 缺陷,剔除 2 类虚构,双侧门禁全绿
- [x] 测试 12 用例(防火墙 16 场景 / JWT 回路 4 / 图拓扑 6)
- [x] CI 四门禁 workflow;契约管线激活(openapi.json 入库)
- [x] 仓库 git 版本化

## 验收

- [x] 全链路:web 5173 → api 8000 → agent 8001 → pg 5432(15 表)→ redis 6379
- [x] 安全冒烟:内部通道无令牌 403 / DROP 被 AST 拦截 / /auth/me 无 token 401
- [x] 质量门禁:api+agent 各 ruff/mypy strict/import-linter/pytest;web tsc/eslint/build
- [x] 契约幂等:两次导出 openapi.json 哈希一致

## 遗留(转后续变更;均已在后续变更中落地,2026-10-09 快照清点时补勾)

- [x] 认证闭环(user store + login + auth spec 同步编写)—— 下一个变更（auth 域包与 specs 已在库）
- [x] /internal/sql/execute 数据源执行路由(连接池 + data_scope + 审计落库)（chat/toolcall 服务实装,sql-execution spec 在库）
- [x] Agent 节点 LLM 接线(understand/generate/summarize)（apps/agent graph/nodes.py 实装）
- [x] 前端主布局(会话列表 + 对话区 + RBAC 菜单)（apps/web 主布局与管理页已落地）
- [x] 远程仓库与 push(待用户指令)（origin 已配置,HEAD 与远端一致,ls-remote 核验）
