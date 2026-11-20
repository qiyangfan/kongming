# 变更提案：质量门禁缺口收口（分层契约 + liannu）

## Why

门禁盘点发现：`models`/`repository`/跨域 router 的依赖方向没有机器判定；仓库缺 pre-commit，
本地提交前无 gitleaks/ruff 快速反馈；`packages/liannu` 的 pyright 与独立包边界未接门禁。

## What Changes

1. **import-linter**：新增三条契约——models 不依赖 service/router/repository；repository
   不反向依赖 service/router；业务域 router 不 import 他域 router（跨域装配走
   dependencies/adapters）。为满足契约拆出 `auth/deps.py`、`chat/wiring.py`、
   `bot/agent_resolver.py` 等组合根模块。
2. **pre-commit**：gitleaks + ruff 0.17.1（与 uv.lock 对齐）。
3. **liannu 门禁**：pyright strict（sqlglot 私有导出按 stub 限制豁免）、import-linter
   「不依赖消费方 kongming」、覆盖率 70 已由 pytest-cov 执法；接入 Makefile/CI。

## Non-goals

- 不改业务语义；路由路径、请求/响应模型与权限点保持不变。
- 不引入新的运行时依赖。

## Impact

- 34 个路由/依赖文件的 import 调整；新增组合根模块；CI 仅增门禁步骤。
