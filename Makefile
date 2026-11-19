.PHONY: check api-check agent-check liannu-check web-check contracts contracts-check secret-scan dead-code dup-check deps-audit lint-config up down

check: api-check agent-check liannu-check web-check contracts-check dead-code dup-check deps-audit lint-config

# ruff format 是 black 的兼容实现(单一格式化器,不另引 black);覆盖率经
# pytest-cov fail_under 一票否决(阈值在各 pyproject 的 [tool.coverage.report])
# 结构门禁:模块行数(1000)/类公有方法数(20)/局部变量(15)/返回数(6)/
# 语句数(50)/祖先数(7)/布尔表达式数(5)/循环 import 交给 pylint(全部工具默认阈值),
# ruff 无对应稳定规则;其余 lint 仍归 ruff。
api-check:
	cd apps/api && uv run ruff format --check . ../../packages/liannu && uv run ruff check . ../../packages/liannu && uv run pylint --disable=all --enable=too-many-lines,too-many-public-methods,too-many-locals,too-many-return-statements,too-many-statements,too-many-ancestors,too-many-boolean-expressions,cyclic-import --jobs=0 kongming && uv run mypy kongming ../../packages/liannu/liannu && uv run pyright && uv run lint-imports && uv run pytest -q --cov

# agent 测试用 httpx2:环境 NO_PROXY 含 ::1 会令代理白名单解析失败,测试内固定白名单
agent-check:
	cd apps/agent && uv run ruff format --check . && uv run ruff check . && uv run pylint --disable=all --enable=too-many-lines,too-many-public-methods,too-many-locals,too-many-return-statements,too-many-statements,too-many-ancestors,too-many-boolean-expressions,cyclic-import --jobs=0 kongming_agent && uv run mypy kongming_agent && uv run pyright && uv run lint-imports && NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 uv run pytest -q --cov

# 连弩(053 SQL 安全引擎):独立 dev 环境全量门禁(对拍基线回放 + 覆盖率)
liannu-check:
	cd packages/liannu && uv run ruff format --check . && uv run ruff check . && uv run pylint --disable=all --enable=too-many-lines,too-many-public-methods,too-many-locals,too-many-return-statements,too-many-statements,too-many-ancestors,too-many-boolean-expressions,cyclic-import --jobs=0 liannu && uv run mypy liannu && uv run pytest -q --cov

web-check:
	cd apps/web && pnpm typecheck && pnpm lint && pnpm lint:css && pnpm format:check && pnpm test:run && pnpm build && pnpm size && pnpm e2e

# 死代码:vulture 只取 100% 置信度(动态注册/数据类误报不进结果);
# LangGraph 回调参数名由框架签名规定,按名豁免;TS 侧 knip 以 entry 图判定。
dead-code:
	cd apps/api && uvx --from vulture==2.16 vulture kongming --min-confidence 100
	cd apps/agent && uvx --from vulture==2.16 vulture kongming_agent --min-confidence 100 --ignore-names new_versions,task_path
	cd packages/liannu && uvx --from vulture==2.16 vulture liannu --min-confidence 100
	cd apps/web && pnpm exec knip

# 复制粘贴:jscpd 默认 50 tokens,门禁线=重复率 5%(超出即非零);只扫生产路径
dup-check:
	npx --yes jscpd@4.3.0 apps/api/kongming apps/agent/kongming_agent packages/liannu/liannu apps/web/src --threshold 5 --format python,typescript,tsx,css --ignore '**/*.gen.ts'

# 依赖漏洞:必须在各自项目环境里审计(uvx 会审到工具自身环境)
deps-audit:
	cd apps/api && uv run --with pip-audit==2.10.1 pip-audit --skip-editable --progress-spinner off
	cd apps/agent && uv run --with pip-audit==2.10.1 pip-audit --skip-editable --progress-spinner off
	cd packages/liannu && uv run --with pip-audit==2.10.1 pip-audit --skip-editable --progress-spinner off
	cd apps/web && pnpm audit

# 配置与脚本:workflow(含 shellcheck)/Dockerfile/shell/YAML/Markdown/TOML/工作流安全
lint-config:
	actionlint
	hadolint infra/api.Dockerfile infra/agent.Dockerfile
	shellcheck infra/example-data/load.sh
	uvx --from yamllint==1.38.0 yamllint -c .yamllint.yml .
	npx --yes markdownlint-cli2@0.24.1
	uvx codespell==2.4.3 README.md AGENTS.md docs
	cd apps/web && pnpm dlx @taplo/cli@0.7.0 fmt --check ../../apps/api/pyproject.toml ../../apps/agent/pyproject.toml ../../packages/liannu/pyproject.toml ../../.gitleaks.toml
	uvx zizmor==1.32.0 .github/workflows

# 秘密扫描(gitleaks 默认规则 + 仓内允许项);CI 独立作业强制,本地需自行安装 gitleaks
secret-scan:
	gitleaks detect --source . --config .gitleaks.toml --redact --no-banner

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
