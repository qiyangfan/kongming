# HANDOFF — Kongming 交接文档

> 本文是项目级事实库,不是执行日记:只写下一棒需要的内容——当前口径、可复现配方、
> 常见坑、未完成事项。变更明细看 git log 与 `openspec/changes/archive/` 工件;
> 工作区状态看 `git status`。每次收尾只更新受影响条目,文档过长按四块收敛重写:
> 「当前状态 / 下一步 / 开放决策项 / 验收记录」。

## 一、当前状态

### 1.1 代码与部署现态

- 平台形态:Python 3.13 双进程(`apps/api` 控制面持全部凭证,`apps/agent` 零凭证
  经 `/internal/*` 回调)+ React 19 SPA(`apps/web`);PostgreSQL 18 + pgvector、
  Redis;`packages/liannu` 为 SQL 安全引擎共享包(uv path 依赖,当前 0.1.3)。
- 迁移 head:**0063**(`0063_document_chunk_entities.py`)。新环境
  `alembic upgrade head` 即含全部能力;数据访问是显式 SQL、无 ORM metadata。
- 组合根与拆分后的模块位置(结构治理后):MCP 出站工厂入口
  `kongming/mcp/router.py`、`bot/adapters.py`、`chat/adapters.py`、
  `toolcall/dependencies.py`;chat 会话/导出簇在 `chat/session_service.py`、
  流式编排在 `chat/stream_runner.py`;RAG 管理簇在 `rag/admin_service.py`;
  task 协作账本 `task/collab.py` + `task/collab_repo.py`、看门狗
  `task/watchdog.py`、派发 `task/dispatch.py`;agent 侧诊断
  `diagnostics.py`、意图路由 `graph/intent_nodes.py`、对话直通
  `graph/dialogue.py`。领域分层 router → service → models → repository 不变。
- 现行默认形态:SQLGuard primary(连弩引擎)、答案异步评分开、看门狗开、
  关系扩展开、召回闸恒裁剪形态、文档实体扩展与句界校准缺省关(部署级开关)。
- 前端标准件与宪法纪律见 `AGENTS.md` 与聚合目录 `../FRONTEND-CONSTITUTION.md`;
  `ui/` 与共享组件先改本仓再 `bash ../scripts/sync-frontend-standard.sh`。

### 1.2 门禁构成与门禁实测值

`make check` 一条命令跑完全部本地门禁;CI 另有一套按路径裁剪的作业(见下)。

- **api**(`make api-check`):ruff format --check + ruff check(规则族含
  E/F/I/UP/B/S/ASYNC/C4/C901/PERF/PLR/RET/RUF/SIM/TRY/BLE/DTZ/G/LOG/T20/PTH/
  ICN/RUF022,RUF100 失效 noqa 即报错)+ pylint 结构集(模块行数 1000、类公有
  方法 20、局部变量 15、返回 6、语句 50、祖先 7、布尔表达式 5、循环 import、
  错误类;E1101 因装饰器/再导出误报关闭)+ mypy strict + pyright strict
  (含 tests 与根 `scripts/`)+ import-linter(27 契约)+ deptry + Alembic
  单头断言 + `pytest --cov`。
- **security-cov**(`make security-cov`,CI api 作业在 pytest 之后调用同一目标):
  分域覆盖率地板 auth 76 / sqlfw 85 / guardrail 79 / datascope 90 /
  datasource 76 / tenant.py 84 / sql_util.py 与 escape_util.py 各 97;
  全局 `fail_under=70` 保留,分域地板防高覆盖模块替关键域兜底。只升不降。
- **agent**(`make agent-check`):同 api 的静态结构 + 单头无关;`NO_PROXY`
  在目标内固定为 `localhost,127.0.0.1`。
- **liannu**:ruff + pylint 结构集 + mypy + deptry + pytest(含对拍基线回放)。
- **web**(`make web-check`):tsc(含 e2e 与 playwright 配置)+ eslint
  (sonarjs 推荐集、类型感知集、Testing Library、Playwright)+ madge 环检测 +
  stylelint + prettier + vitest 覆盖率门槛 + build + size-limit + e2e。
- **contracts-check**:导出 OpenAPI 并断言 `contracts/openapi.json` 无差异,
  再生成 `apps/web/src/lib/api-types.gen.ts` 断言无差异(漏跑 `make contracts`
  会被拦)。
- **dead-code**:vulture 100% 置信度(api/agent/liannu/scripts)+ knip。
  **dup-check**:jscpd `--threshold 5`(超阈值才非零)。**deps-audit**:
  pip-audit 在各项目环境内跑(uvx 会审到工具自身)+ `pnpm audit`。
  **license-check**:只扫运行时闭包与 pnpm --prod,白名单见
  `scripts/license_check.py`。**lint-config**:actionlint/hadolint/shellcheck/
  yamllint/markdownlint/taplo/zizmor + `pin_check.py`(FROM 必须钉 sha256)+
  `link_check.py`(md 相对链接)。**lock-check**:`uv lock --check` ×3 +
  `pnpm install --frozen-lockfile`。**compose-check**:`podman compose config -q`。
  **secret-scan**:gitleaks(本地需自行安装)。
- **CI 作业**(`.github/workflows/ci.yml`):changes 路径过滤 + secrets +
  api/agent/liannu/web/contracts/images + dup + supply-chain;另有 codeql 与
  eval-nightly。与本地差异:CI pyright 为最新版;api/agent/liannu 上传
  coverage.xml 并在 PR 跑 diff-cover(新代码行 ≥80%);contracts 在 PR 跑
  oasdiff 破坏性检测(`--fail-on ERR`);images 构建 api/agent/sandbox 并
  trivy 扫 IaC 与镜像、产 CycloneDX SBOM;supply-chain 四路 license_check;
  api 作业 postgres 服务为 `pgvector/pgvector:pg18`(与 compose 同版);
  pnpm 固定 11.24.0(与 `packageManager` 一致)。
- **评估劣化红线**:`scripts/eval_redline.py` 从评估输出解析宽松/严格/表命中、
  judge 通过率、retrieval 指标;基线同层且 prompt 指纹一致时任一指标掉 ≥5pp
  即失败;跨层/跨指纹/首跑只记录。eval-nightly 要求
  `KM_AGENT_LLM_API_KEY` 与 `KM_EVAL_ADMIN_PASSWORD` 两个 secret,评估步骤
  显式 `set -euo pipefail`(否则 `| tee` 吞 pytest 退出码)。
- **门禁实测(2026-11-20 单条 `make check`)**:api 1721 通过 / 10 跳过 /
  0 失败,覆盖率 76.78%;安全域实际 79/88/82/93/79/87/100/100;agent 613;
  liannu 23;web vitest 183 + e2e 2,typecheck/eslint/madge/stylelint/prettier/
  build/size 全绿;contracts 零差异;dead-code/dup(2.2%)/deps/license/
  lint-config/lock/compose/secret 全绿。
- **基线对照口径**:拆分层验收基线为 1691/10/5(失败集合=4 个联邦出站 +
  1 个 STT 模型环境项);Makefile 固定 `NO_PROXY` 白名单后本机 api-check
  为 0 失败。对照历史数字时先确认口径与失败集合是否一致。

### 1.3 不变量测试(门禁的机器判定面)

- `tests/test_route_auth_coverage.py`:除 `/healthz` 外所有路由必须挂
  `current_user`(含经 `require_perm` 的);公开端点须显式登记。
- `tests/test_route_contract.py`:所有 APIRoute 显式声明 `response_model`;
  OpenAPI 生成后 operation_id 全局唯一(重名会让客户端方法互相覆盖)。
- `tests/test_env_example_sync.py`:Settings 字段与 `.env.example` 双向同步;
  compose 引用的每个 `KM_*` 变量必须在模板登记。
- `tests/test_trust_boundary_compose.py`:compose 的 agent 服务与 agent 镜像
  不得出现数据库/Redis 环境变量(ADR-002 构造级保证)。
- `tests/test_migration_conventions.py`:拦截 PG ENUM、状态列 SMALLINT/INTEGER
  (含 SQLAlchemy 写法)、无时区 TIMESTAMP/`sa.TIMESTAMP()`/`sa.DateTime()`、
  SERIAL 自增主键。`tests/test_migration_roundtrip.py`:全链降 base 后重升,
  验无残留表且回到 head。
- `tests/test_module_import_py313.py`:定义时求值位置禁现 TYPE_CHECKING 专有
  名字(3.14 惰性注解会掩盖,3.13 容器 import 即炸)。
- web:`src/test/theme-tokens.test.ts`(`:root` 每个 token 必须在 `.dark` 有值,
  安全区两个豁免)、`src/test/import-layers.test.ts`(分层方向)、
  `src/test/constitution.test.ts`(禁十六进制色值/原生表单控件/emoji/
  `window.confirm` 与 `alert`/新增样式表,登录页品牌图标与隐藏 file input
  是登记豁免)。
- `scripts/link_check.py` 与 `scripts/eval_redline.py` 的回归测试在
  `tests/test_gate_scripts.py`。
- 测试禁外网:api/agent conftest 的 autouse 回环守卫拦非回环 TCP;api conftest
  每条用例后取消残留后台任务(跨用例 fire-and-forget 会撞出顺序相关的假红)。

### 1.4 已裁决不采用的规则(逐条裁决,非待办)

- `TRY003`(每个抛出点自建异常类)、`PLW0603`(命中的全是模块级惰性单例)、
  `RUF003`(中文标点对中文注释误报)、`SLF001`(私有靠约定,pyright 已在
  类型层把关)。
- pylint R0913/R0917/R0902(参数/属性/返回个数,开启等于全仓参数对象化改造,
  对既有签名无缺陷检出增益)。
- 变异测试(无稳定基线,IO/异步路径解释力低)、Python 认知复杂度(web 侧已由
  sonarjs 覆盖)、schemathesis、视觉回归、性能基线。
- knip 忽略 `components/ui/**`(双仓逐字节标准件)与生成文件;vulture 豁免
  LangGraph 回调参数名;openspec 全量校验中存量「需求文本过长」只告警。

### 1.5 环境事实与本机配方(最易踩的坑)

1. **本机是 podman 不是 docker**;`docker` 只是交互 shell 的 alias。
2. **本地 pg/redis 用统一环境栈 `~/workspace/env`**(docker-compose 在目录内,
   port 5432/6379,redis 免密;本地起服务/跑测试不需要注入 KM_REDIS_URL 与
   KM_DATABASE_URL,conftest 缺省即正确)。**勿 `make up` 自起**(那是容器
   部署形态,会与 env 栈抢 5432)。角色 `kongming` 已升 SUPERUSER(临时演练库
   建 vector 扩展需要)。
3. 服务端口:api 8000 / agent 8001 / web dev 5173;容器冒烟栈让到 18080/18081
   (宿主机 8000/8001 常被其他项目占用)。
4. **`NO_PROXY` 陷阱**:环境里若含 `[::1],::1`,httpx 解析代理白名单会抛
   `Invalid port`——agent 23 个测试失败由此而来;api 的 4 个联邦出站测试在
   无外网环境同样失败。Makefile 的 api-check/agent-check 已在命令内固定
   `NO_PROXY=localhost,127.0.0.1`,手搓 pytest 时必须同样设置。本机系统代理
   常开,全仓 httpx 出站统一 `trust_env=False`(代理会架空 SSRF 钉扎与内网
   回调;新增出站点必须带上)。
5. **apps/api/.env 与 apps/agent/.env 存在**,settings 的 `env_file` 是 cwd
   相对——进程从仓库根启动才读根 `.env`;根 `.env` 只供 compose 插值。
   手起 agent 须把 api 的 `KM_INTERNAL_TOKEN` 以 `KM_AGENT_INTERNAL_TOKEN`
   注入(前缀不同),否则 api→agent 一律 403。**禁止整份 source 根 .env 跑
   pytest**(会盖掉 conftest 缺省,内部端点全 403)。
6. 冷启动:env 栈起 pg/redis → `cd apps/api && uv run alembic upgrade head` →
   分别起 api/agent。健康检查 `curl 127.0.0.1:8000/healthz`、
   `curl 127.0.0.1:8001/healthz`。
7. **测试库占位上限**:PG 的 DROP COLUMN 只标记,单表占位硬上限 1600。迁移
   roundtrip 一律用 `tests/conftest.py::migrated_tmp_db()`(一次性临时库),
   禁止在共享 `kongming_test` 上跑升降级循环。`kongming_test` 占位超 800 应
   `DROP DATABASE kongming_test WITH (FORCE)` 重建,重建后须重跑 zhparser
   两件套(见 8),否则中文分词集成测试转 skip。
8. **中文分词(zhparser)启用配方**(PGDG 无包,容器内源码编译):编译 SCWS
   与 zhparser(用 codeload tarball,git 协议被重置)→ 重启 postgres →
   各库执行 `CREATE EXTENSION zhparser;` + `CREATE TEXT SEARCH CONFIGURATION
   zhparser (PARSER = zhparser);` + `ALTER TEXT SEARCH CONFIGURATION zhparser
   ADD MAPPING FOR n,v,a,i,e,l WITH simple;` → 设
   `KM_RAG_FULLTEXT_TS_CONFIG=zhparser` 重启 api → 逐数据源重扫回填存量 tsv →
   经 recall-probe 复标全文路阈值。回退设为 `simple` 再重扫。
9. **评估栈坑**:api 启动必带 `KM_CHAT_QUOTA_DAILY_CALLS=0`(否则日配额在
   中后段 429,分数假跌);逐题 429/403 会让失败题混入总分,判定前先 grep
   逐题日志的「异常」;两轮之间清 `query_history` 与 `km:ragctx:*`;自建评估
   账号(`KM_EVAL_ADMIN_USERNAME/PASSWORD`),勿猜现有凭据。judge 账号必须
   绑定租户且对象归属该账号(`/tasks` 按归属过滤,SUPER_ADMIN 不等于可见面)。
10. **浏览器实测**:GUI 用 Playwright,本机 Chromium 下载可能停滞,改用已装
    Chrome(`chromium.launch({ channel: "chrome" })`);`VITE_API_PROXY_TARGET`
    可临时改代理端口。流式答案断言先剥离问题气泡自身文字(关键词假阳性)。
11. 仓库迁移后旧 venv 假健康:`uv sync` 显示 Checked 但 `uv run` 报
    Failed to spawn——`rm -rf apps/*/.venv packages/liannu/.venv && uv sync`。
12. 上游参考项目 `/Users/solvela/nl2sql-data-platform`:只参考逻辑不复制代码,
   其容器/卷/网络绝不触碰。
13. muniu 网关(127.0.0.1:8787)按 ADR-007 是**可选端点**,官方直连是一等
    公民;当前根 `.env` 的 LLM key 直连百炼,embedding 需显式
    `dimensions=2560`(默认 1024)。
14. 可选能力均为部署级条件注册,缺配置=零存在感:检索精排
    (`KM_RERANK_PROVIDER/MODEL/API_KEY`)、联网检索
    (`KM_WEBSEARCH_PROVIDER/API_KEY`)、文档实体扩展(`KM_DOC_ENTITY_*`)、
    STT(模型在位即用,缺失端点 503;预下载 small 模型一次约 480MB)。
    组合残缺时 prod 拒启、dev 告警降级。
15. 本机可能有并发 pytest 进程共用 `kongming_test`(约每十分钟一轮):
    表现为随机的 401/DBAPIError/`.coverage` 被改写。判红前先 `ps` 查并发,
    单独重跑定案;`security-cov` 建议先 `cp .coverage /tmp/...` 快照再跑。
16. **非交互 shell 的 PATH**:后台命令/工具 shell 默认没有 mise shims 与
    podman,`make check` 会先停在 web-check(`pnpm: command not found`),
    再停在 compose-check(`podman: No such file or directory`)。先
    `export PATH="/opt/podman/bin:$HOME/.local/share/mise/shims:/opt/homebrew/bin:$PATH"`
    再跑;这是环境问题,不是代码回归。

### 1.6 命令速查

```bash
make check          # 全量门禁(api/agent/liannu/web + contracts + 供应链/配置)
make contracts      # 导出 OpenAPI + 生成前端类型(改 api 路由/模型后必跑)
make security-cov   # 安全域分域覆盖率地板(消费 api-check 产出的 .coverage)
# 服务:apps/api → uv run uvicorn kongming.main:app --port 8000
#       apps/agent → uv run uvicorn kongming_agent.main:app --port 8001
#       apps/web → pnpm dev(5173,proxy /api → 8000)
# 迁移:apps/api → uv run alembic upgrade head
# 评估:KM_EVAL_ADMIN_PASSWORD=… uv run pytest evals -m smoke|full|multiturn
#      (evals/ 不进默认收集,无凭证整层 skip)
# 推送前另跑:PYRIGHT_PYTHON_FORCE_VERSION=latest uv run pyright(CI 同款)
```

### 1.7 关键教训(代码里看不出来的操作知识)

流程与门禁:

- `make check` 后台跑必须看显式 `MAKE-EXIT:` 行(包装 shell 的退出码是最后
  一条命令的);管道验证取 `PIPESTATUS[0]`。
- 共享测试库并行 pytest 会互锁挂死(零 CPU),先停自己那份错峰重跑定性。
- 改 api 后契约必须重生成并提交;`contracts-check` 末段按提交后语义设计,
  「产物已更新未提交」态必报差属机制性红。
- openspec 归档:specs 已手工同步时必须 `--skip-specs`(否则 delta 双套用
  被拒;MODIFIED 整块替换会误删现有场景)。无行为变化的变更在
  `.openspec.yaml` 写 `skip_specs: true`。
- 会话级 alembic upgrade 的 fileConfig 会把收集期已 import 的 logger 置
  disabled;caplog 断言日志的测试须先 `logger.disabled = False`。
- 时间敏感测试禁用「replace(day=N) ± 相对日」算式(每月固定日期必挂),
  固定回退天数 ± 半日。
- `# type: ignore[code]` 压不住 pyright 的参数级定位;ignore 要移到报错
  参数行,mypy 与 pyright 双跑裁决。
- 验收不能只看 tasks.md 勾选,要在真实调用路径上确认(曾出现全部勾选但
  注入函数零调用方)。

LLM/异步/协议:

- 工具回调通道 status 恒 executed,业务 verdict 在 result 内(去重/熔断按
  verdict 收)。
- httpx 超时是单次操作间隔而非总时长;`asyncio.wait_for` 抛 TimeoutError
  ⇔ 内部 task 被取消,以此区分超时来源。
- LLM 应答畸形(空 choices)必须收敛到 LlmError + 归 ERROR,embed 应答校验
  index 集合等价。
- AsyncOpenAI 客户端不可跨事件循环复用;共享 AsyncSession 的多 repo 查询
  必须顺序 await。
- api 调 agent 的应答解析先取文本再显式解析,异常收敛到该用例的降级分支。
- Python 3.14 惰性注解会掩盖 TYPE_CHECKING latent bug(见 1.3 的 AST 测试)。

SQL/PG:

- `sqlglot` 深嵌套抛 RecursionError,不在 SqlglotError 族;范围过滤条件
  LEFT JOIN 必须放 ON 内;超长中文列唯一索引用 md5 表达式索引,ON CONFLICT
  需表达式逐字匹配;asyncpg 读 json/jsonb 常回 str,裸读必须归一化;
  None 参数需 CAST 形态;并发 UPSERT 用 advisory lock + 单语句 CTE。

安全:

- SSRF DNS rebinding 防护=自定义 transport 钉扎判定 IP + Host 头保原域;
  白名单命中不构成私网豁免;重定向不跟随。
- 自由文本进三引号围栏前替换围栏序列;别名/文件名类校验结构字符;上传文件名
  过护栏规则;敏感凭证字段 `repr=False`,待确认凭证原文终态清零。

前端/浏览器:

- Tailwind v4 任意值 `calc()` 的 `+` 两侧写 `_`;绝对定位 `inset-0` 做响应式
  改型四元式必须成组还原;`h-16` 与 `pb-safe` 同元素会吃掉内容高度(分层);
  svg 显式钉 `size-*` 才能覆盖 `[&_svg:not([class*='size-'])]`。
- `bg-*` 类拼错或 token 未注册时 Tailwind 静默不生成规则,tsc/eslint/stylelint
  都查不出,接线以 `vite build` 产物 CSS 复核。
- 5xx 触发 react-query 重试,断言错误态要等够 7-8s;HMR 保留本地 state,
  评审读数以 fresh navigate 为准;StrictMode 双 effect 只在 dev 出现。

### 1.8 流程与资产纪律

- 功能开发走 openspec(`/opsx:propose` → apply → archive),提案必须有
  Non-goals;一次只保持一个活动实施变更;安全规则类(SQL 防火墙/RBAC/租户)
  测试先行。
- prompt 资产:`apps/agent/kongming_agent/graph/prompts.py` 与 `nodes.py`
  的 LLM 模板为上游逐字移植(全角标点),改动前读文件头例外清单;
  `test_prompts.py` 断言防手滑;titler.py 是自研资产不适用。
- 评分两侧同源演化:`kongming/chat/scoring.py` 的权重表与 agent
  `scorer.py` 的分级口径不得单边漂移。
- 文档/ADR/注释一律简体中文;`.env` 从 `.env.example` 复制;`uv.lock` 与
  `pnpm-lock.yaml` 必须入库。

## 二、下一步

1. **统一身份联邦(活动变更 `2026-11-20-unified-identity-oidc`,未实施)**。
   前置条件:虎符(`longzhong/hufu`)交付 issuer/JWKS/授权码 + PKCE 端点,
   且 owner 明确批准进入实施;解除前不动本地认证面(本地账密、OAuth 5 家、
   RS256 签发、refresh 家族全部保留为默认)。实施顺序按该变更 tasks.md
   第 1-6 节,先红后绿,条目 6.4 要求 make check 全绿后归档。
2. **eval-nightly 首次真实运行核对**:推送后在 Actions 配置两个 secret
   (`KM_AGENT_LLM_API_KEY`、`KM_EVAL_ADMIN_PASSWORD`)并手动
   `workflow_dispatch`;首跑无基线只记录不判罚,确认「评估统计」段被
   `eval_redline.py` 正确解析后再建立基线。
3. **CI 推送后观察点**:api/agent/liannu/web/contracts/images 六作业 +
   secrets/dup/supply-chain;重点看 diff-cover(新代码行 ≥80%)与
   oasdiff(PR 上破坏性变更)。
4. **产品待办**以 `docs/roadmap.md`「产品待办登记」为单源(含否定裁决
   登记),不在本文复制清单。

## 三、开放决策项

已定死决策(不再议,引用即终):

| 主题               | 结论                                                                                                                 | 出处                                |
| ------------------ | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| 技术栈             | Python 3.13 双进程(api 持凭证 / agent 零凭证)+ React 19 SPA;不引入 Keycloak/Next.js/微服务/消息队列/Litestar         | ADR-001                             |
| 信任边界           | 进程边界构造级;agent 禁 DB 凭证(env/驱动双禁);合并进程=违宪                                                          | ADR-002 + 宪法 Article IV           |
| 认证               | 自建(argon2id + PyJWT RS256 + authlib 联邦连接器);联邦 IdP 为可选 provider(ADR-007 身份权威演进)                     | ADR-003/006/007                     |
| Schema             | baseline 制;UUID v7 / VARCHAR+CHECK / timestamptz;新表/改表走新迁移                                                  | ADR-004                             |
| 契约               | FastAPI OpenAPI 唯一事实源;前端类型生成禁手写;CI 漂移 diff                                                           | ADR-005                             |
| 分层               | 域包 router→service→models→repository;domain 零框架依赖                                                              | 宪法 Article II                     |
| 执行路由           | verdict 三态;绑定复校;data_scope=最外层 WHERE AST 注入;控制面保留表拒读;只读池连接级只读                             | sql-execution + datascope spec      |
| 问答链路           | agent 8 节点图 + ReAct 内核双轨;verdict 对外四态;权限类不重试;重试上限=初始之外 2 次                                 | nl2sql + nl2sql-agent + chat spec   |
| RAG/知识库         | 四元唯一键 + HNSW/GIN;三路召回 RRF;召回零命中回退内省;成功 SQL 回写;覆盖优先级 INTROSPECTION<AI<UPLOAD<EDIT<FEEDBACK | rag + knowledge spec                |
| 评估体系           | Execution Match 双分引擎;宽松=黄金指标;evals 进程外隔离;query_history 幂等治理                                       | eval + ai-eval spec                 |
| Bot/商业化         | Bot 定义+权限逐请求求交;OpenAI 兼容端点;BOT 维度配额+mock 支付闭环                                                   | custom-agent + payment + team spec  |
| 多模型/观测        | 注册表 env JSON(条目级 protocol)+任务路由;协议翻译收敛 llm_protocol.py;失败调用也采集                                | multi-model-routing + observability |
| LLM 网关           | muniu 为可选端点,官方直连一等公民                                                                                    | ADR-007(2026-09-26 修订)            |
| 前端 Markdown 渲染 | react-markdown + remark-gfm/breaks;不解析原始 HTML                                                                   | ADR-009                             |
| 挂账治理           | 开放挂账=零;新发现项一律给终态(修/关闭/条件触发/产品登记)                                                            | backlog-settlement 终态块           |

挂账终态:开放挂账=零。条件触发登记(触发即评估,非开放债务):

- 注入拆字/全角变体绕过:触发=出现真实绕过事件或规模化信号。
- MCP 远端错误文案反射面收紧:触发=生态滥用信号。
- 多 worker 三项(池失效广播/归档 advisory lock/调度互斥):触发=实际多
  worker 部署。
- `/mcp` chunked 无 Content-Length 形态的闸边界:触发=网关层限流设计。
- 流式/同步端点泛异常与客户端断开窗口的收集器用量丢失:触发=流式 bot 落地。
- `/query/stream` 老链路附件预处理仍在 handler 段:老流式挂收集器时同步迁移。
- 辅助数据查询(tenants/myTeams)失败静默:触发=用户反馈。

产品登记(非债务)与否定裁决(Slack/zhparser 切默认/真实支付/OCR 引擎不做)
统一见 `docs/roadmap.md`;本文不复制。

## 四、验收记录

每条只保留验收证据,过程见 git log 与归档工件。

- **2026-11-20 门禁补全**:`make check` 单条绿(数值见 1.2);补齐 CPD/死代码/
  依赖/镜像/配置 lint、前端覆盖率阈值与 E2E、回环网络守卫、后台任务清理、
  安全域覆盖率地板、迁移 DDL/往返、信任边界编排层、内链与评估红线脚本。
- **2026-11-20 结构治理归档**:`split-oversized-routers-and-services` 拆
  7 个超长 router 四件套 + 3 个 service 用例簇;验收=全量 pytest 对照基线
  不新增失败 + contracts 零 diff + `make check` 全绿 + 手工冒烟(同步/流式/
  断线续存/导出/EXPLAIN/会话管理)全过;工件在
  `openspec/changes/archive/2026-11-20-split-oversized-routers-and-services/`。
- **2026-11-07 SAG 三变更**:检索评测探针 + 事件-实体扩展 + 实体路入池
  门槛;A/B 终值 off=1.000、调优后 on=1.000(门槛前 0.938),对照行不作
  红线,部署保持缺省关;工件 `archive/2026-11-07-*`。
- **2026-11-04 DeepSeek 三变更**:任务看门狗(缺省开,stale 300s)、事件立项
  task 目标、worker 续读(8KB 钳制);迁移 0062;工件 `archive/2026-11-04-*`。
- **2026-11-03 评分/兜底/观测三变更**:异步评分(0059)、兜底分类(0060)、
  stage_trace 观测(0061);工件 `archive/2026-11-03-*`。
- **2026-10-20 四变更**:工单/Siku 域退役、速率窗口平滑、halfvec(2560)
  向量升级(0057/0058)、MCP 官方 SDK2 收编;工件 `archive/2026-10-*`。
- **2026-10-18/19**:检索精排、图级检查点、协作分工名册、全栈刷新
  (Vite 8/React 19/eslint 10)、MCP 出站熔断;工件 `archive/2026-10-1*`。
- **2026-10-12 至 2026-09-24 各批**(圆桌拓扑、HTTP 连接器、全文路兜底、
  授权状态单一闸、CUSTOM 数据范围、偏好自管理、移动端重设计、SSL 光闸等):
  工件与验收数字见 `openspec/changes/archive/` 对应目录。
