.PHONY: check api-check agent-check liannu-check web-check admin-check contracts contracts-check secret-scan dead-code dup-check deps-audit license-check lint-config lock-check compose-check security-cov spec-check up down

check: api-check agent-check liannu-check web-check contracts-check dead-code dup-check deps-audit license-check lint-config lock-check compose-check security-cov secret-scan spec-check

# ruff format 是 black 的兼容实现(单一格式化器,不另引 black);覆盖率经
# pytest-cov fail_under 一票否决(阈值在各 pyproject 的 [tool.coverage.report])
# 结构门禁:模块行数(1000)/类公有方法数(20)/局部变量(15)/返回数(6)/
# 语句数(50)/祖先数(7)/布尔表达式数(5)/循环 import 交给 pylint(全部工具默认阈值),
# ruff 无对应稳定规则;其余 lint 仍归 ruff。
api-check:
	cd apps/api && uv run ruff format --check . ../../packages/liannu ../../scripts && uv run ruff check . ../../packages/liannu ../../scripts && uv run pyright ../../scripts && uv run pylint --disable=all --enable=too-many-lines,too-many-public-methods,too-many-locals,too-many-return-statements,too-many-statements,too-many-ancestors,too-many-boolean-expressions,cyclic-import,E --disable=E1101 --jobs=0 kongming && uv run mypy kongming ../../packages/liannu/liannu ../../scripts && uv run pyright && uv run lint-imports && uvx deptry==0.25.1 . && test "$$(uv run alembic heads | wc -l | tr -d ' ')" -eq 1 && NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 uv run pytest -q --cov

# 安全域分模块覆盖率地板:全局 fail_under=70 允许高覆盖模块替低覆盖模块兜底,
# 这里给认证/SQL 防火墙/护栏/数据域/凭据存储各设下限(实测值留 3pp 余量,只升不降)。
# 消费 api-check 产出的 .coverage:须在 api-check 之后串行执行(单独调用时复用现有数据);
# CI api 作业在 pytest 之后调用同一目标。
security-cov:
	cd apps/api && uv run coverage report --include='kongming/auth/*' --fail-under=76
	cd apps/api && uv run coverage report --include='kongming/sqlfw/*' --fail-under=85
	cd apps/api && uv run coverage report --include='kongming/guardrail/*' --fail-under=79
	cd apps/api && uv run coverage report --include='kongming/datascope/*' --fail-under=90
	cd apps/api && uv run coverage report --include='kongming/datasource/*' --fail-under=76
	cd apps/api && uv run coverage report --include='kongming/tenant.py' --fail-under=84
	cd apps/api && uv run coverage report --include='kongming/sql_util.py' --fail-under=97
	cd apps/api && uv run coverage report --include='kongming/escape_util.py' --fail-under=97

# api/agent 测试均含出站调用:环境 NO_PROXY 含 ::1 会令代理白名单解析失败,测试内固定白名单
agent-check:
	cd apps/agent && uv run ruff format --check . && uv run ruff check . && uv run pylint --disable=all --enable=too-many-lines,too-many-public-methods,too-many-locals,too-many-return-statements,too-many-statements,too-many-ancestors,too-many-boolean-expressions,cyclic-import,E --disable=E1101 --jobs=0 kongming_agent && uv run mypy kongming_agent && uv run pyright && uv run lint-imports && uvx deptry==0.25.1 . && NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 uv run pytest -q --cov

# 连弩(053 SQL 安全引擎):独立 dev 环境全量门禁(对拍基线回放 + 覆盖率)
liannu-check:
	cd packages/liannu && uv run ruff format --check . && uv run ruff check . && uv run pylint --disable=all --enable=too-many-lines,too-many-public-methods,too-many-locals,too-many-return-statements,too-many-statements,too-many-ancestors,too-many-boolean-expressions,cyclic-import,E --disable=E1101 --jobs=0 liannu && uv run mypy liannu && uvx deptry==0.25.1 . && uv run pytest -q --cov && cd ../../apps/api && uv run lint-imports --config ../../packages/liannu/pyproject.toml && uv run pyright -p ../../packages/liannu

web-check:
	cd apps/web && pnpm typecheck && pnpm lint && pnpm dlx madge@8 --circular --extensions ts,tsx src && pnpm lint:css && pnpm format:check && pnpm test:cov && pnpm build && pnpm size && pnpm e2e

# 管理台独立门禁(make check 保持只覆盖用户 web;发布前单独执行)
admin-check:
	cd apps/admin && pnpm install --frozen-lockfile && pnpm typecheck && pnpm lint && pnpm lint:css && pnpm format:check && pnpm test:run && pnpm build && pnpm e2e

# 死代码:vulture 只取 100% 置信度(动态注册/数据类误报不进结果);
# LangGraph 回调参数名由框架签名规定,按名豁免;TS 侧 knip 以 entry 图判定。
dead-code:
	cd apps/api && uvx --from vulture==2.16 vulture kongming --min-confidence 100
	cd apps/agent && uvx --from vulture==2.16 vulture kongming_agent --min-confidence 100 --ignore-names new_versions,task_path
	cd packages/liannu && uvx --from vulture==2.16 vulture liannu --min-confidence 100
	uvx --from vulture==2.16 vulture scripts --min-confidence 100
	cd apps/web && pnpm exec knip

# 复制粘贴:jscpd 默认 50 tokens,门禁线=重复率 5%(超出即非零);只扫生产路径
dup-check:
	npx --yes jscpd@4.3.0 apps/api/kongming apps/agent/kongming_agent packages/liannu/liannu apps/web/src packages/ui/src packages/api-client/src --threshold 5 --format python,typescript,tsx,css --ignore '**/*.gen.ts'

# 依赖漏洞:必须在各自项目环境里审计(uvx 会审到工具自身环境)
deps-audit:
	cd apps/api && uv run --with pip-audit==2.10.1 pip-audit --skip-editable --progress-spinner off
	cd apps/agent && uv run --with pip-audit==2.10.1 pip-audit --skip-editable --progress-spinner off
	cd packages/liannu && uv run --with pip-audit==2.10.1 pip-audit --skip-editable --progress-spinner off
	cd apps/web && pnpm audit

# 许可证:python 只扫 uv 运行时闭包(dev 工具不随镜像分发),前端扫 --prod 依赖;
# 白名单=MIT/BSD/Apache/ISC/PSF/MPL-2.0/0BSD/Zlib/CC0,组合许可逐原子判
license-check:
	cd apps/api && uv run --with pip-licenses==5.5.1 python ../../scripts/license_check.py python .
	cd apps/agent && uv run --with pip-licenses==5.5.1 python ../../scripts/license_check.py python .
	cd packages/liannu && uv run --with pip-licenses==5.5.1 python ../../scripts/license_check.py python .
	cd apps/web && python3 ../../scripts/license_check.py node .

# 配置与脚本:workflow(含 shellcheck)/Dockerfile/shell/YAML/Markdown/TOML/工作流安全
lint-config:
	actionlint
	hadolint infra/api.Dockerfile infra/agent.Dockerfile infra/sandbox.Dockerfile
	python3 scripts/pin_check.py infra
	shellcheck infra/example-data/load.sh
	uvx --from yamllint==1.38.0 yamllint -c .yamllint.yml .
	npx --yes markdownlint-cli2@0.24.1
	python3 scripts/link_check.py .
	uvx codespell==2.4.3 README.md AGENTS.md docs
	cd apps/web && pnpm dlx @taplo/cli@0.7.0 fmt --check ../../apps/api/pyproject.toml ../../apps/agent/pyproject.toml ../../packages/liannu/pyproject.toml ../../.gitleaks.toml
	uvx zizmor==1.32.0 .github/workflows

# 锁文件一致性与编排语义(uv --frozen 不校验清单与锁的同步)
lock-check:
	cd apps/api && uv lock --check
	cd apps/agent && uv lock --check
	cd packages/liannu && uv lock --check
	cd apps/web && pnpm install --frozen-lockfile

compose-check:
	podman compose --env-file .env -f infra/docker-compose.yml config -q

# 秘密扫描(gitleaks 默认规则 + 仓内允许项);CI 独立作业强制,本地需自行安装 gitleaks
secret-scan:
	gitleaks detect --source . --config .gitleaks.toml --redact --no-banner

# 契约漂移校验(与 CI contracts job 同参,单源=本目标):导出 openapi 并断言
# 产物无未提交差异——改 api 路由/模型后必跑;漏跑会被此闸拦下(055 教训:
# 曾只有 CI 兜底,本地全绿仍漂移)
## spec-check: OpenSpec 规格/变更 strict 校验(工具缺失时跳过)
spec-check:
	@if command -v openspec >/dev/null 2>&1; then openspec validate --all --strict; else echo "[spec-check] openspec 未安装,跳过"; fi

contracts-check:
	cd apps/api && uv run python scripts/export_openapi.py
	git diff --exit-code contracts/openapi.json
	# 前端类型由 spec 生成,漏跑契约只拦 spec 不拦产物:再生成一次并断言无差异
	cd apps/web && pnpm exec openapi-typescript ../../contracts/openapi.json --output ../../packages/api-client/src/api-types.gen.ts
	# 流事件运行时校验模块同属生成产物:漏生成即拦
	cd apps/web && node scripts/gen-sse-validator.mjs
	git diff --exit-code packages/api-client/src/chat-stream-validate.gen.js packages/api-client/src/chat-stream-validate.gen.d.ts
	git diff --exit-code packages/api-client/src/api-types.gen.ts

contracts:
	cd apps/api && uv run python scripts/export_openapi.py
	cd apps/web && pnpm contracts:gen

up:
	podman compose --env-file .env -f infra/docker-compose.yml up -d postgres redis

down:
	podman compose --env-file .env -f infra/docker-compose.yml stop
