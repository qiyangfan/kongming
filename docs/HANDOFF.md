# HANDOFF — Kongming 交接文档

> **本文定位**:面向新会话的开工指引,只记「代码里看不出来」的当前事实。
> 不堆变更日志——那是 git 的职责。变更明细三处可查:git log 提交信息、
> `openspec/changes/archive/`(每变更 proposal/design/tasks/spec delta 工件)、
> 文档历史版本(`git show <提交>:docs/HANDOFF.md`)。
> 维护纪律:每变更收尾只更新「当前状态」一行与必要的教训/终态条目;
> 逐变更长段一律不进本文。

## 当前状态(2026-09-24)

- **056–059 批量落地+评审循环修复,已归档(2026-09-24)**:056 skill-pack
  (垂类内容包四段契约/按数据源启停/合并投影封顶/`skill_` 保留前缀/
  `system:skill:manage`;fewshot SQL 只作提示文本);057 numeric-grounding
  (派生量白名单+确定性审计剥离+compute_delta/compute_share 本地执行,
  `tool:compute`);058 react-todo-honesty(多子目标 Todo 诚实账本,预算
  退出不批量 DONE);059 user-preference-memory(`user_prefs` 闭合槽位,
  隐式只写槽位 patch、显式分流槽位/NOTE、偏好注入带「可忽略/不约束
  数据范围」前缀)。同批评审循环坐实并修复 12 处缺陷(审计剥空回退未审
  原文、asyncpg jsonb 回 str 导致 prefs/skillpack 读空、metrics 重启用
  自冲突、PrefsIn 未知键静默丢弃、时间列 str 排序错序、059 注入/路由
  假完成等),契约已再生。**059 MemoryPage 槽位编辑 UI 仍未开工**(任务
  3.4 显式阻塞,前端待做)。
- **054 gates-env-parity 已归档(2026-09-24)**:CI 补齐 pyright 步骤
  (api/agent 与 make check 同参),`.env.example` 对齐 muniu 网关与
  SQLGUARD 模式开关;「make check × CI」对照表入本文门禁节。CI 实跑绿
  (含 pyright)后归档,「本地绿 CI 假绿」面闭合。
- **053 仍在 SQLGuard 影子观察期(切主条件见下)**:引擎为
  本仓 monorepo 共享包 `packages/liannu`(连弩;Python 单遍,与内置路径同源
  sqlglot AST,v0.1.2;两进程 uv path 依赖引入,**2026-09-12 晚由独立仓
  km-sqlguard + vendor wheel 并入,vendor 机制退役**——独立性检验五问
  不占,迁移验证=引擎 21 例全绿+121 例现算与冻结基线逐字节一致),
  api/agent 默认 shadow 模式(内置权威,引擎并行判定只记录)。**冒烟证据
  (2026-09-12)**:流式问答「商品总共有多少个」全链路 PASS(答案 4,与
  goods 4 行一致),两进程启动行 `SQLGUARD 管线 mode=shadow,活跃引擎=
  liannu version=0.1.2,启动自检通过`,请求期零 ENGINE_FALLBACK 零影子
  不一致,sys_sql_audit 落 PASS 行;对拍基线 121 例(sqlfw/datascope/
  evals 冻结,`apps/api/scripts/export_sqlguard_fixtures.py` 重复运行
  diff 为空)引擎回放零不一致。切主 = 两进程 `.env` 置
  `KM_SQLGUARD_MODE=primary`(agent 前缀 `KM_AGENT_SQLGUARD_MODE`)+
  重启;影子不一致归零且 ≥1 周无 ENGINE_FALLBACK 后归档。深嵌套默认
  上限 32(实测 256 不可行,引擎实现形态改 Python、仓库形态并入 monorepo
  等实施期修订见 change design「Decisions(实施期修订)」节)。
- **muniu 网关接线完成(ADR-007,2026-09-11)**:agent 面 LLM 出口已全部指向
  muniu 网关(127.0.0.1:8787;主模型/embedding/vision 注册表 `.env` 三处切换),
  直连冒烟、e2e 问答(verdict PASS)、网关计量与计价实证全过;供应商真实 key
  已收敛到 muniu 侧。**灰度一周进行中(自 2026-09-11 起计时)**:每日对账
  (muniu 仓 `scripts/reconcile.sql`,差异 ≤1%),期满达标 ADR-007 转「已接受」;
  回滚=`.env` 三处改回直连(切换前备份 /tmp/.km-agent-env-pre-muniu.bak,
  重启即失)。运行细节见「环境事实」11。
- 001–052 + backlog-settlement 已全部归档;054–059 已于 2026-09-24 归档
  (活动变更仅剩 053 影子观察)。上游 41 域全覆盖。近期批次:2026-08-29 四连(043 生成纪律规则 10+哨兵短路/
  044 NO_DATA 四态中性形态/045 保留表收编 user_memory/046 投影 id
  收敛+列级哨兵);2026-08-30 二连(047 检索治理/048 聚合追问与去重
  护栏);**2026-08-30 三连(049 图表候选化[列角色画像+suggest_charts
  有序候选 ≤3,契约纯增 chart_candidates,两维分组→分组柱状图,pie
  可加闸,标识/期间列恒为维度]/050 内部 id 暴露治理[compute_stats
  统计资格过滤+humanize_stats 键名中文化+displayUser 昵称→用户名→
  已注销回退链,user_username 契约纯增]/051 实体核对判据[规则 10 表级
  哨兵操作化+对位反例,evals expect_no_data 负例形态+default.yaml ×2,
  001 进 smoke])**;**2026-09-11(052 知识覆盖报告[GET
  /system/rag/coverage-report:四类空白确定性盘点=零覆盖表/纯结构登记/
  缺标签列/失败问题簇+few-shot 概况,零 LLM 零写入纯读、封顶计数如实,
  失败簇读 chat_message 四态∪差评∪纠正 SQL 跨域只读(feedback 先例),
  内省失败不产出半份报告;契约纯增 coverage-report;LLM Wiki gap
  detection 方法论移植、其生成式编译已否决——非确定性生成物入知识面
  与 041/051 毒知识同源])**。
- 051 收尾同批:query_history goods 硬凑毒沉淀清偿(精确 id,保留合法
  语义行);infra/example-data 新增 comments-ext.sql(sakila_ext 独立库
  中文注释,**只能对 sakila_ext 执行**,严禁混入 comments.sql 在 kongming
  库跑)+load.sh 第 6 步挂载;重扫实证 PG comment→知识链路生效(存量
  AI_GENERATED 条目按优先级不覆盖,刻意设计)。终态教训:毒沉淀清理与
  prompt 重启生效之间存在窗口期,事故会重新落库——清理+验证后必须复查
  零残留;sakila_dyn 挂 default 租户(datasource_key 实查,勿按租户 key
  想当然)。
- 评估基线(evals/README 基线表):2026-08-30——单轮宽松 50/50、严格
  35/50(含 2 题负例不计严格分)、多轮 12/12、动态 4/4、表命中 48/50。
  基线纪律触发面已扩至知识/文档变更(08-23 知识再生成+文档灌入晚于旧
  基线致零提示词变更亦漂移的混杂教训);两轮评估之间须清 ctx 缓存。
- 工作区/提交状态以 `git status` 与 `git log` 为唯一事实源
  (规约:文档不写提交哈希、不谈提交形态)。
- **开放挂账=零**(backlog-settlement 终态化裁决,见「挂账终态」节)。
- 下一棒:053 影子达标后切主并归档;可选做 059 MemoryPage 槽位编辑 UI;
  其余按需新域,无既定排期项。

## 新会话开工指引

1. 先读本文与 `AGENTS.md`;动敏感区前按 AGENTS.md「前置阅读」表读对应 ADR。
2. 功能开发走 openspec 流程(`/opsx:propose` → apply → archive);
   提案必须有 Non-goals;一次只保持一个活动实施变更。
3. 质量底线:安全规则类(SQL 防火墙/RBAC/租户)测试先行;收工前
   `make check` 全绿 + 冒烟记录;改 api 路由/模型必跑 `make contracts`
   并提交产物;GET 端点必须挂 response_model(ADR-005,已两犯)。
4. **prompt 资产纪律**:`apps/agent/kongming_agent/graph/prompts.py` 与
   `nodes.py` 的 LLM 模板为上游逐字移植(全角标点!),改动前先读文件头
   例外清单;`test_prompts.py` 断言防手滑。titler.py 是自研资产不适用。
5. 上游参考项目 `/Users/solvela/nl2sql-data-platform`(Java COLA)只参考
   逻辑不复制代码;其容器/卷/网络绝不触碰。

## 环境事实(新会话最易踩的坑)

1. **本机是 podman 不是 docker**:`docker` 只是交互 shell 的 alias,
   非交互命令一律用 `podman`。
2. **标准端口**:api **8000** / agent **8001** / web dev **5173** /
   postgres **5432** / redis **6379**。
3. **本地环境冷启动**(幂等,任何"连不上/端口不对"先跑这个):
   ```bash
   make up                                 # podman compose 起 postgres+redis
   cd apps/api && uv run alembic upgrade head
   ```
   pg 数据持久化在 compose 卷 `kongming_pg_data`;删卷/重置属破坏性操作,
   须用户明确授权。
4. 三处 `.env` 已配好(不入库):根(占位密钥)、`apps/api/.env`
   (`KM_INTERNAL_TOKEN=dev-internal-token`)、`apps/agent/.env`
   (**LLM key 已配真值,dashscope qwen-plus**——冒烟/演示即开即用,
   注意勿提交、勿打印)。
5. 可能的后台进程:api(8000)/agent(8001)/web(5173);另有 ngrok 固定
   域名隧道与用户自建 cloudflared 服务(不属本项目,别动)。日志
   /tmp/km-*.log;重启本项目进程前先 `pkill -f "uvicorn kongming"`、
   `pkill -f "kongming/apps/web"`。改完代码要重启服务才生效。
6. 本地 dev:基础设施走 `make up`,两个 Python 服务直接
   `uv run uvicorn ...`,web `pnpm dev`(compose 的 api/agent 服务留完整
   部署用)。
7. **浏览器实测**:ZCode IAB 面板对本机 guest 会卡死——GUI 测试一律用
   Playwright MCP 工具组(`mcp__plugin_playwright_playwright__*`)。
8. **dev 库活数据**:admin 密码 `Kongming#2026`;sakila(film 1000/
   rental 16044/customer 599)与 olist(orders 99441/items 112650)两
   租户已注册绑定 admin;`tenant_default.goods` 小表 4 行 + smoke-self
   用户(SELF 域,密码 `Smoke#2026`)+ `sys_datasource` 行 sakila_dyn
   (同实例**异库**动态源,指向独立库 `sakila_ext`——外部业务库形态,
   不复用 tenant_sakila 租户 schema;装载随 load.sh 第 6 步)+ 5 个联邦
   账号(漆阳帆-dingtalk 等)——均为回归活数据。示例数据源文件
   `.example-data/`(已 gitignore),装载管线 `infra/example-data/load.sh`
   (幂等)。存量环境 sakila_dyn 仍指旧形态(同库 tenant_sakila)的,
   手工一行迁到新落点:`UPDATE public.sys_datasource SET database='sakila_ext',
   default_schema='public' WHERE datasource_key='sakila_dyn';`(密码不变)。
9. 企微联调:ngrok 免费域名与其可信域名校验不兼容;用 cloudflared 快速
   隧道或正式域名;应用可信 IP 白名单随家宽漂移重配。
10. **仓库迁移后旧 venv 假健康**:apps/*/.venv 若建于旧绝对路径
    (如 ~/kongming),迁到 ~/workspace/kongming 后入口脚本 shebang 全
    失效——`uv sync` 仍显示 Checked(假健康),`uv run pytest` 报
    Failed to spawn。处置:`rm -rf apps/*/.venv && uv sync` 重建
    (venv 为可再生构建产物,2026-09-11 052 实证)。
11. **muniu 网关(ADR-007,agent 面 LLM 唯一出口)**:127.0.0.1:8787,
    **podman 后台容器形态**(muniu 仓 `make dev-up` 起/`dev-down` 停/
    `dev-status` 查;宿主交叉编译+scratch 镜像,不依赖 registry 与代理)。
    ⚠ 计量库在 podman **命名卷 muniu-data**(SQLite WAL 在 virtiofs 挂载上
    会 I/O error),宿主查询走 muniu 仓 `make dev-sql`(拷出+sqlite3,
    对账 reconcile.sql 用法不变);容器日志 `podman logs muniu-gateway`。
    **管理面对账凭证(055)**:`KM_MUNIU_ADMIN_TOKEN`(对账端点拉 muniu
    `GET /admin/v1/usage` 用,与 muniu 容器 `MUNIU_ADMIN_TOKEN` 同源;
    环境变量注入,不落库不入日志)。
    ⚠ 切换后 agent `.env` 的 KM_AGENT_LLM_API_KEY 已是 muniu 的 mk- key
    而非 dashscope key;上游 key 真值经 muniu 仓 `.env.local`(gitignore)
    注入容器,重签 key 需同步 agent `.env` 两处(主 key+registry qwen-vl
    条目)并重启 agent。管理面/控制台同端口 8787(admin token=本地 dev 值
    `muniu-dev-admin-token`,与 KM_INTERNAL_TOKEN 同级,仅本机)。
12. **liannu 引擎共享包**(`packages/liannu`,053;原独立仓 km-sqlguard 已并入):
    SQL 安全引擎,uv path 依赖。升版流程:包内 `pyproject.toml` 改版本号 →
    同步两进程 `pyproject.toml` 的 `==<版>` 声明 → `uv lock && uv sync`
    (path 依赖 editable 生效,无 wheel 缓存坑);对拍基线回放走根
    `make check` 的 `liannu-check`。历史教训仍有效:**常驻 `uv run uvicorn`
    服务持有 uv 缓存锁时 `uv cache clean` 会超时**——升版本号路线可绕开。

## 命令速查

```bash
make check          # 全门禁(api/agent ruff+mypy+lint-imports+pytest + web tsc+eslint)
make contracts      # 导出 OpenAPI + 生成前端类型(改 api 后必跑)
# 服务: apps/api → uv run uvicorn kongming.main:app --port 8000
#       apps/agent → uv run uvicorn kongming_agent.main:app --port 8001
#       apps/web → pnpm dev(5173,proxy /api → 8000)
# 迁移: apps/api → uv run alembic upgrade head
# 评估: KM_EVAL_ADMIN_PASSWORD=… uv run pytest evals -m smoke|full|multiturn
#      (evals/ 不进默认收集,无凭证整层 skip)
# 冒烟: curl 127.0.0.1:8000/healthz;curl 127.0.0.1:8001/healthz
```

## 已定死的决策(不再议,引用即终)

| 主题 | 结论 | 出处 |
|---|---|---|
| 技术栈 | Python 3.13 双进程(api 持凭证 / agent 零凭证)+ React 19 SPA;不引入 Keycloak/Next.js/微服务/消息队列/Litestar | ADR-001 |
| 信任边界 | 进程边界构造级;agent 禁 DB 凭证(env/驱动双禁);合并进程=违宪 | ADR-002 + 宪法 Article IV |
| 认证 | 自建(argon2id + PyJWT RS256 + authlib 联邦连接器);未来企业 SSO 走 IdP 下游回退 | ADR-003/006 |
| Schema | baseline 制;UUID v7 / VARCHAR+CHECK / timestamptz;已规划能力列一次建全;新表/改表走新迁移 | ADR-004 |
| 契约 | FastAPI OpenAPI 唯一事实源;前端类型生成禁手写;CI 漂移 diff | ADR-005 |
| 分层 | 域包 router→service→models→repository;domain 零框架依赖 | 宪法 Article II |
| 执行路由 | verdict 三态(PASS/BLOCKED/ERROR);绑定复校;data_scope=最外层 WHERE AST 注入;控制面保留表拒读+危险函数拒用;只读池连接级只读 | sql-execution + datascope spec |
| 问答链路 | agent 8 节点图(老链路)+ ReAct 内核(019)双轨;verdict 对外四态(PASS/BLOCKED/ERROR/NO_DATA,044 如实说明);权限类不重试;重试上限=初始之外 2 次 | nl2sql + nl2sql-agent + chat spec |
| RAG/知识库 | rag 两表(四元唯一键+HNSW+GIN);三路召回 RRF top5+query_history 独立 few-shot;召回零命中回退内省;成功 SQL 回写;覆盖优先级 INTROSPECTION<AI<UPLOAD<EDIT<FEEDBACK;别名(037)=生效值同源重算派生文本 | rag + knowledge spec |
| 评估体系 | Execution Match 双分引擎;宽松=黄金指标且不惩罚序错;evals 进程外工具 testpaths 隔离;query_history 幂等治理 | eval + ai-eval spec |
| prompt 质量 | 生成/理解/摘要三层规则(统计不加 LIMIT/tiebreaker/ROUND/投影完备+技术主键默认不进投影/列级哨兵两级如实说明/指代保真/事实性接地);prompt 文本迭代经验=点名自我合理化的反例比抽象纪律条遵从度高;gold 规范十条与生成侧互锁 | nl2sql-agent spec |
| Bot/商业化 | Bot 定义+权限逐请求求交;OpenAI 兼容端点;BOT 维度配额+mock 支付闭环(核销单点 reconcile);团队归属可见性纯函数 | custom-agent + payment + team spec |
| 多模型/观测 | 注册表 env JSON+任务路由+出口白名单;用量采集 fire-and-forget+bot 收集器双通道;失败调用也采集(status/latency) | multi-model-routing + observability spec |
| LLM 网关 | agent 面 LLM 出口(主模型/embedding/vision 注册表)统一经 muniu(127.0.0.1:8787,OpenAI 兼容),供应商 key 收敛 muniu 单点;回滚=.env 三处改回直连,零成本 | ADR-007 |
| 挂账治理 | 开放挂账=零;新发现项一律给终态(修/关闭/条件触发/产品登记),不留无排期「随域延后」 | backlog-settlement 终态块 |

## 挂账终态(开放挂账=零,2026-08-25 backlog-settlement 裁决)

**明确关闭(结案)**:A-B10 confirm 硬熔断(write_burst 写闸实证兜底)/
B2-B9 http 响应头闸(h11 16KB 默认兜底已实证+注释固化)/审计页 user 筛选
UI(API 已备场景低频)/D-B7 进度条动态 style(原裁接受)。

**条件触发登记(触发即评估,非开放债务)**:
- 注入拆字/全角变体绕过:触发=出现真实绕过事件或规模化信号。
- 支付回调先查单后验签探测面:触发=接真实公网支付通道。
- MCP 远端错误文案反射面收紧:触发=生态滥用信号。
- 多 worker 三项(池失效广播/归档 advisory lock/调度互斥):触发=实际多 worker 部署。
- /mcp chunked 无 CL 形态的实测闸边界(body 已缓冲,属应用层输入约束
  非完整 DoS 防护):触发=网关层限流设计。
- 流式/同步端点泛异常与客户端断开窗口的收集器用量丢失(「响应携带」
  契约的边界;bot 当前走同步端点暴露面有限):触发=流式 bot 落地。
- /query/stream(老链路)附件预处理仍在 handler 段(与 react 流式移入
  gen 的形态不一致;残余异常面≈0 仅编程错误):老流式挂收集器时同步迁移。
- 辅助数据查询(tenants/myTeams)失败静默:核心已降级显式,其余触发=用户反馈。

**产品登记(非债务;已落 docs/roadmap.md「产品待办登记」节)**:STT/OCR
选型、真实支付通道、可视化画布、MCP resources/prompts、Bot 版本化上架、
个性化推荐、用户/TEAM 维度配额、触发器失败重试、更多事件源、中文全文
zhparser、知识库团队归属(出处 032①)。

**出处说明**:终态主清单见 archive/2026-08-25-backlog-settlement/proposal.md;
「触顶部分结果的 web 渲染增强」「辅助数据查询失败静默」两条为 R1/R2 评审
期新增裁决(本节即出处)。

**历史明细**:各域逐项挂账原文见 git 历史版本文档与 archive 工件。

## 关键教训精选(代码里看不出来的操作知识)

流程与门禁:
- 会话级 alembic upgrade 的 `fileConfig`(disable_existing_loggers)会把
  **收集期已 import** 的模块 logger 置 disabled——新测试用 caplog 断言日志,
  夹具里先把目标 logger `disabled = False`(053 test_sqlfw_engine 实证)。
- make check 后台跑必须看 **make-exit 行**(包装 shell 的 exit code 是
  最后一条命令的,曾有整库迁移炸了仍假绿的实证);后台命令内自拼
  `echo MAKE-EXIT:$?` 同样会假绿——用 `make …; echo EXIT:$?` 同行或
  前台直跑。pyright 全量含 tests/,只对 kongming/ 子集跑绿不代表门禁绿。
- **`# type: ignore[code]` 压不住 pyright 的参数级定位**(054 补 CI pyright
  首跑实证):mypy 把 kwargs 类型错汇报到调用尾部行,pyright(strict)逐参数
  行报——ignore 挂在 trace_id 行压不住 api=/llm= 行的错,22 处全数暴露。
  修法 = 把 ignore 精确移到报错参数行(mypy strict 含 unused-ignores,
  不能无脑加,移完双跑 mypy+pyright 裁决)。另:管道验证退出码必须取
  `PIPESTATUS[0]`,`cmd | tail; echo $?` 取到的是 tail 的(本轮实证假绿)。
- **共享测试库并行 pytest 互锁**:两个 pytest 同时跑会零 CPU 挂死
  (非卡测试)——先停自己那份错峰重跑判归属;单挂用例先单独重跑再定性。
- **知识层事故定性先核租户/数据源维度**:default 源是共享路径(各租户
  各自登记同 key 知识),按 datasource_key 查到的行未必属于事发租户;
  042 曾据此误判「残留毒知识」两轮修正——真实根因在生成层(LLM 凭
  先验编表),rag 召回隔离本身无恙。
- **勿跑 ruff format**(全仓非 format 管理,误跑产生海量格式噪音)。
- 测试模块真建只读池的,teardown 必须 `dispose_read_engines()`(池残留
  污染后续模块断言);测试 override 禁裸开 get_sessionmaker(耗尽主池)。
- 受控行为变更必须同步对齐既有测试断言(字段钉集断言两犯)。
- 时间敏感测试禁「replace(day=N)-timedelta(相对日)」算式(偏移随日数
  变号,每月固定日期必挂;用固定回退天数±半日)。
- ruff noqa 对多行隐式字符串拼接须挂块首物理行。
- 改 api 后契约必须重生成并提交;运行时与入库 openapi 逐对象比对防漂移。
- **asyncpg 读 json/jsonb 常回 str**(system.parse_json_agg 先例):text()
  裸读必须归一化解码,`isinstance(dict)` 直接丢会静默读成空(056/059
  评审实证 prefs/skillpack content 双双中招)。
- **禁用不删指标 + 启用前同名冲突检测 ⇒ 重启用自冲突**:冲突判定须按
  「同名且定义一致=幂等放行」,否则第一次启用成功、第二次永远 409
  (056 skillpack metrics 实证)。
- **任务勾选 ≠ 代码落地**:059 的注入通道/显式路由曾全勾但 `preference_hint`
  零调用方、extract 仍吐自由文本——收口验收必须跑通链路,不能只看 tasks。

LLM/异步/协议:
- 工具回调通道 status 恒 executed,**业务 verdict 在 result 内**(去重/
  熔断集按 verdict 收,否则诱导 LLM 编造)。
- httpx 超时=单次操作间隔而非总时长(慢滴必须 wait_for 包总预算);
  `asyncio.wait_for` 抛 TimeoutError ⇔ 内部 task 被其取消——内源超时
  透传时 task 未取消,以此区分超时来源(workflow 引擎范式)。
- LLM 应答畸形(空 choices)须收敛 LlmError+归 ERROR 采集,不能裸
  IndexError;embed 应答校验 index 集合等价而非仅条数。
- AsyncOpenAI 客户端池跨事件循环不可复用(缓存按 loop 维度+weakref 校验)。
- 共享 AsyncSession 的多 repo 查询必须顺序 await(并行会话状态未定义)。
- api 调 agent 回调的应答解析须防非 JSON/畸形(resp.json() 裸抛会违约
  降级契约;先取文本再显式解析,异常收敛到该用例的降级分支)。
- evals「Server disconnected」成片失败=本地 VPN 切换环境事件(TUN 路由
  重建波及回环),勿误判限流勿长冷却,网络稳定后重跑。

SQL/PG:
- sqlglot 深嵌套抛 RecursionError **不在** SqlglotError 族(except 须扩面)。
- LEFT JOIN 的范围过滤条件必须放 ON 内(放 WHERE 把补零桶滤掉)。
- 超长中文文本列唯一索引用 md5 表达式索引;ON CONFLICT 要求表达式逐字
  匹配(裸列/表达式失配 42P10)。
- asyncpg None 参数需 CAST 形态防类型推断失败;uuid 列绑 UUID 对象。
- 并发 UPSERT 唯一约束竞态用 advisory lock+单语句 CTE 两件套
  (READ COMMITTED 下 wCTE 快照竞态消不掉)。

安全:
- SSRF DNS rebinding 防护=自定义 transport 钉扎判定 IP+Host 头保原域+
  `extensions["sni_hostname"]`(证书按原域校验)。
- 免费文本进三引号围栏前必须替换围栏序列(提前闭合=注入逃逸)。
- 别名/文件名类自由文本校验结构字符(换行可伪造表名注入);上传文件名
  过护栏 pii_*/cred_*/inj_ 规则(拒绝优于脱敏:脱敏名撞唯一键)。
- 敏感凭证字段 api_key 用 repr=False;PENDING_CONFIRM 凭证原文终态清零。

前端/浏览器:
- Playwright MCP:textarea 用原生 setter+input 事件(React 受控组件 fill
  偶发不触发);Radix 下拉/菜单 pointerdown 派发+evaluate DOM click;
  react-query 失效重渲染后快照 ref 过期,click 静默落空——改 evaluate
  派事件;短时窗口采样用 evaluate 内 setTimeout 轮询。
- 5xx 触发 react-query retry(约 7-8s 后才 isError,断言错误态要等够)。
- `useParams` 无参数=undefined,与流发起的 `?? null` 不同——守卫比较前
  必须归一(新会话首问死锁根因)。
- HMR/Fast Refresh 保留组件本地 state,评审读数以 fresh navigate 后为准。

## 铁律提醒(全局,详见 ~/.zcode/AGENTS.md 与仓库 AGENTS.md)

- git 写操作(add/commit/push)逐次等用户明确指令,只做字面授权那一步。
- 旧项目容器/卷/网络禁碰;任何不可逆操作前二次确认;运维指令绝不升级强度。
- 安全规则类改动测试先行;文档注释一律简体中文。
- 前端基础交互组件一律 shadcn/ui;同类元素 ≥2 处必抽公共组件;用户点名
  一个 UI 问题=该类全站范式问题,先归类再批量修。

## 门禁对照表(make check × CI,054)

> 增删任一侧门禁时双向核对本表,防止单边增删再现(054 立表动机:pyright 曾是本地专属门禁,CI 假绿)。

| 门禁 | 本地命令 | CI job | CI 步骤 | 差异说明 |
|---|---|---|---|---|
| api 静态+测试 | `make api-check`(ruff format/check + mypy + **pyright** + lint-imports + pytest --cov) | `api` | 同左全序(pyright 于 2026-09-13 补齐) | 无 |
| agent 静态+测试 | `make agent-check`(同上结构) | `agent` | 同左全序(pyright 同批补齐) | 无 |
| liannu 引擎 | `make liannu-check`(ruff+mypy+pytest,含对拍基线回放) | `liannu` | 同左 | 无 |
| web | `make web-check`(tsc+eslint+stylelint+prettier) | `web` | 同左 | 无(前端无单测,三仓共知短板) |
| 契约漂移 | `make contracts-check`(已并入 check;055 教训后补齐) | `contracts` | `make -C ../.. contracts-check`(单源) | 无 |
