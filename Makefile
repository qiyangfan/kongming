.PHONY: check api-check agent-check web-check contracts up down

check: api-check agent-check web-check

api-check:
	cd apps/api && uv run ruff check . && uv run mypy kongming && uv run pytest -q

agent-check:
	cd apps/agent && uv run ruff check . && uv run mypy kongming_agent && uv run pytest -q

web-check:
	cd apps/web && pnpm typecheck && pnpm lint

contracts:
	cd apps/api && uv run python scripts/export_openapi.py
	cd apps/web && pnpm contracts:gen

up:
	docker compose -f infra/docker-compose.yml up -d postgres redis

down:
	docker compose -f infra/docker-compose.yml stop
