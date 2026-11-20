# Tasks

## 1. 分层契约

- [x] 1.1 import-linter 增 models/repository/跨域 router 三条契约，30 kept / 0 broken
- [x] 1.2 拆出 `auth/deps.py`、`chat/wiring.py`、`bot/agent_resolver.py`，路由依赖收敛到组合根

## 2. 本地与包门禁

- [x] 2.1 `.pre-commit-config.yaml`（gitleaks v8.30.1 + ruff v0.17.1），validate-config 通过
- [x] 2.2 liannu pyright strict 0 errors；import-linter 独立包契约 KEPT；覆盖率 90.37%（阈值 70）

## 3. 验证

- [x] 3.1 `make api-check` exit 0（ruff/pyright/mypy/pylint/import-linter/deptry/pytest）
- [x] 3.2 `make liannu-check` exit 0
- [ ] 3.3 推送后 CI 实跑（外部账单/授权）
