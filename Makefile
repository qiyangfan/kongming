.PHONY: check api-check agent-check liannu-check web-check contracts contracts-check up down

check: api-check agent-check liannu-check web-check contracts-check

# ruff format 是 black 的兼容实现(单一格式化器,不另引 black);覆盖率经
# pytest-cov fail_under 一票否决(阈值在各 pyproject 的 [tool.coverage.report])
api-check:
	cd apps/api && uv run ruff format --check . ../../packages/liannu && uv run ruff check . ../../packages/liannu && uv run mypy kongming ../../packages/liannu/liannu && uv run pyright && uv run lint-imports && uv run pytest -q --cov

agent-check:
	cd apps/agent && uv run ruff format --check . && uv run ruff check . && uv run mypy kongming_agent && uv run pyright && uv run lint-imports && uv run pytest -q --cov

# 连弩(053 SQL 安全引擎):独立 dev 环境全量门禁(对拍基线回放 + 覆盖率)
liannu-check:
	cd packages/liannu && uv run ruff format --check . && uv run ruff check . && uv run mypy liannu && uv run pytest -q --cov

web-check:
	cd apps/web && pnpm typecheck && pnpm lint && pnpm lint:css && pnpm format:check

# 契约漂移校验(与 CI contracts job 同参,单源=本目标):导出 openapi 并断言
# 产物无未提交差异——改 api 路由/模型后必跑;漏跑会被此闸拦下(055 教训:
# 曾只有 CI 兜底,本地全绿仍漂移)
contracts-check:
	cd apps/api && uv run python scripts/export_openapi.py
	git diff --exit-code contracts/openapi.json

contracts:
	cd apps/api && uv run python scripts/export_openapi.py
	cd apps/web && pnpm contracts:gen

up:
	podman compose --env-file .env -f infra/docker-compose.yml up -d postgres redis

down:
	podman compose --env-file .env -f infra/docker-compose.yml stop
