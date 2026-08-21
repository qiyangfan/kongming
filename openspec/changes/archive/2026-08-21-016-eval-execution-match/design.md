# 016 设计:Execution Match 双分引擎与评估集

## Context

上游 `/Users/solvela/nl2sql-data-platform/nl2sql-agent/tests/eval/`(test_eval.py
+ conftest.py + 5 份 yaml + README)是本设计的参考原型。上游形态:pytest 三层
marker(smoke/full/multiturn)+ psycopg 直连 PG 双执行(gold 与 gen 各自执行)
+ Keycloak password grant 登录 + 匹配器(严格 multiset / 宽松双路径)。Kongming
重写而非移植的原因:认证体系不同(自建 JWT)、进程拓扑不同(api+agent 双进程,
上游 Java+Python)、响应契约不同(Kongming 链路响应已含执行结果)、以及宪法
约束(agent 面零凭证——评估器不能放 agent 侧)。

## D1 评估器位置与门禁隔离

评估器放 **`apps/api/evals/`**(与 `tests/` 平级):

- 需要凭证:登录密码(真实用户链路)+ PG 直连(gold 执行)——api 侧持凭证,
  agent 面被 ADR-002 宪法禁止持有任何 DB 凭证,评估器不可能放 agent。
- 与 `tests/` 平级而非其内:`tests/conftest.py` 会为测试会话建 kongming_test
  库铺全量表——评估器不需要(它打真实服务与真实库),混入会带来无谓的基建
  耦合与收集风险。
- **默认门禁隔离**:`pyproject [tool.pytest.ini_options]` 加 `testpaths =
  ["tests"]`——`pytest -q`(make check/CI)只收 tests/;评估显式
  `uv run pytest evals -m smoke|full|multiturn` 运行(命令行参数覆盖
  testpaths)。markers 三枚注册进 pyproject。
- `pythonpath = ["."]`:tests/ 下的匹配器单测可 `from evals.matchers import ...`
  (匹配器单测进默认门禁,ai-eval spec「CI smoke 守护」条款的现实化兑现)。
- mypy 保持只查 `kongming` 包(评估器为工具代码,ruff 全量覆盖其风格);
  ruff check . 天然覆盖 evals/。

## D2 评估数据流(Kongming 形态)

```
登录(POST /auth/login,admin+密码 → access JWT)
  └→ 每题:POST /chat/query
       headers: Authorization + X-Tenant-Id
       body:    {question, history?, datasource_key?, persist: false}
       ← ChatQueryOut{sql, success, verdict, columns, rows, ...}
gold_sql:asyncpg 直连 PG 执行(显式 schema 限定,同控制面 DSN)
比较:matchers.result_set_loose_match(gold vs gen)双分
```

- **persist=false**:评估不落会话/消息/不触发标题改写与反馈可见性——零业务
  数据污染(eval spec「评估运行形态」立约)。实现核验:writeback 挂
  `and persist` 条件(009 既有,测试通道不沉淀),评估跑动**不回写
  query_history**,dev 库零污染。但历史冒烟/人工问答的沉淀仍在——README
  记录清场命令(评估重复性口径:prompt 类改动后先清同题 query_history+
  km:ragctx:* 缓存,011 教训)。
- **生成侧结果来源的抉择**:Kongming 链路响应已含执行结果(columns/rows,
  截断 1000),评估器**不重执行 gen SQL**而取响应结果——比上游多验一层
  (链路执行本身即被评估),且天然规避「响应 sql 带 data_scope 绑定参数
  (`:km_ds_0`)无法脱离 api 进程执行」的坑。代价:双侧类型异源(见 D3),
  以及 1000 行截断口径(gold 侧以编写规范兜底:结果集 ≤1000 行,eval spec
  已立约)。
- **超时**:单题 httpx timeout=150s(agent_timeout_sec=120 覆盖全图+网络余量)。
- **401 重登**:access TTL 15min,full+multiturn 全量可能超窗,遇 401 重登
  重试一次(上游同款教训)。

## D3 类型归一化层(Kongming 新增关键逻辑)

上游双执行同源(psycopg 两侧),Kongming 双侧异源:

- gold 侧(asyncpg 直连):DB 原生类型——Decimal/int/float/str/datetime/
  date/None/bool;
- gen 侧(链路响应 JSON):api `_json_cell` 已归一化(Decimal→float),JSON
  形态——float/int/str/None/bool,datetime 为 ISO 字符串。

匹配前统一过 `_canon_cell`:

| 类型 | gold 侧 | gen 侧 | 归一化 |
|---|---|---|---|
| 数值 | int/float/Decimal(非 bool) | int/float | round(float(v), 6) |
| 时间 | datetime/date/time | ISO 字符串 | isoformat()(gold 侧) |
| None/bool/str | 同 | 同 | 原样 |

数值容差语义与上游一致(1e-6 经 round 实现);时间列评估题尽量避免投影
(规范注明),归一化层兜底形态正确性。匹配器单测覆盖异源形态
(Decimal("1.5") vs 1.5、datetime vs ISO 串)。

## D4 匹配器(matchers.py 纯函数,算法承上游)

- **严格**:双侧归一化后 multiset 精确相等(排序无关,_safe_sort_key 防
  None/str 混排 TypeError)。
- **宽松**(黄金指标)双路径:
  1. **列等价+投影行近似**:gold 列集==gen 列集或 gold⊆gen(大小写不敏感)
     → gen 按 gold 列名投影(兼容列序差异/SELECT *)→ 行集合近似等值
     (数值 1e-6;gold_sql 含 `order by`(不区分大小写子串)保序比较,
     否则 multiset)。
  2. **数值子集**:gold 数值 multiset ⊆ gen 数值 multiset(双指针)——容忍
     列名不兼容的投影差异(gold 投影 id/gen 投影 name、聚合别名差异);
     **纯文本结果集(gold 无数值)只走路径 1**(空集子集恒真误判),
     路径 1 列名不兼容时回退严格集合等值兜底(上游 011 审查修复形态)。
- 保序判定用子串识别(上游同款已知边界:子查询 ORDER BY 也命中——编写
  规范第 5 条「外层 ORDER BY 才算保序」以规范兜底,不改算法)。

## D5 评估集

| 文件 | 内容 | 题量 |
|---|---|---|
| sakila.yaml | single_table×3/filter×3/aggregate×6(含 top-N tiebreaker)/join_2×3/join_3×3/join_4plus×2 | 22(含 smoke×2) |
| olist.yaml | 同构(9 表:orders/order_items/customers/order_payments/order_reviews/products/sellers/geolocation/category_translation) | 18(含 smoke×2) |
| sakila_multi_turn.yaml | 指代/省略/排序延续/过滤追加 | 6 case |
| olist_multi_turn.yaml | 同构 | 4 case |
| datasource.yaml | sakila_dyn 显式 key(tenant=default)×3 + 跨租户拒绝×1 | 4 |

- 用例字段:`id`/`question`/`gold_sql`/`expected_tables`/`category`/可选
  `tenant`/`datasource_key`/`expect_reject`/`smoke`(承上游)。
- gold_sql 显式 schema 限定(`tenant_sakila.film` 形态,评估器直连不设
  search_path)。
- 动态拒绝用例:tenant=sakila + datasource_key=sakila_dyn(属 default 租户)
  → 预期 HTTP 400 DATASOURCE_INVALID(chat 预检同形,与上游
  TENANT_FORBIDDEN 码不同——eval delta 已对齐口径)。
- 多轮:每轮独立 gold_sql,累积问句作 history(≤5 由服务端截断),persist=false。
- 编写规范五条(tiebreaker/业务可读列/连带同步/连跑两次验证/外层 ORDER BY)
  + Kongming 新增三条(结果集 ≤1000 行/避免投影时间戳列/gold 走显式 schema)
  沉淀进 evals/README.md。

## D6 Recall@K(间接口径)

复用 014 feedback 的 `extract_table_names`(sqlglot 抽表,CTE 排除,末段
小写)对生成 SQL 抽表,统计 `expected_tables ⊆ gen_tables` 命中率。间接口径
的裁定:召回中间产物(retrieve 节点的 RAG 命中)不对评估器暴露——表选对是
召回生效的链路可见结果;直接口径(调 /internal/rag/retrieve)测的是组件而
非链路,列 Non-goals 边界。

## D7 query_history 治理(搭车,ADR-004 走新迁移)

- **0007 迁移**:存量去重(同 (tenant_id, datasource_key, question) 多行
  保留 created_at 最新)→ `CREATE UNIQUE INDEX uq_query_history_tdq ON
  rag.query_history (tenant_id, datasource_key, question)`。空表瞬时;
  downgrade 删索引。列零新增。
- **UPSERT**:`insert_history` 改 `INSERT ... ON CONFLICT (tenant_id,
  datasource_key, question) DO UPDATE SET sql_text=EXCLUDED.sql_text,
  embedding=COALESCE(EXCLUDED.embedding, query_history.embedding),
  status='SUCCESS', created_at=now()`——同题覆盖保留最新成功 SQL(消
  014 O4 重复沉淀);embedding 失败保留旧向量(旧向量语义仍有效)。
- **上限**:UPSERT 同事务顺带 `DELETE ... WHERE tenant_id=:t AND
  datasource_key=:d AND id NOT IN (SELECT id ... ORDER BY created_at DESC
  LIMIT :cap)`,cap=500(常量 `QUERY_HISTORY_CAP` 进 rag/models.py)。
  唯一索引前缀 (tenant, ds) 支撑子查询过滤;500 行/租户源量级下 DELETE
  代价可忽略。
- **测试先行**:同题两次回写=一行且 SQL 为最新;embedding 失败保旧向量;
  超限淘汰最旧;不同问句互不影响。既有 writeback 测试回归。

## D8 凭证与配置

评估器环境变量(KM_EVAL_ 前缀,evals/conftest.py 自读,不进 kongming
settings——评估器是工具不是产品配置面):

| 变量 | 缺省 | 说明 |
|---|---|---|
| KM_EVAL_API_BASE | http://127.0.0.1:8000 | api 地址 |
| KM_EVAL_ADMIN_USERNAME | settings.admin_username | 登录账号 |
| KM_EVAL_ADMIN_PASSWORD | (无缺省) | 缺失时整层 skip 并提示(不猜密码) |
| KM_EVAL_PG_DSN | settings.database_url 派生 | gold 直连(+asyncpg 剥 DSN 前缀) |

skip 而非 fail:无凭证环境(CI/新人)显式 `pytest evals` 时得到明确 skip
理由而非红色失败。

## D9 测试策略

- **匹配器单测**(tests/test_eval_matchers.py,进默认门禁):严格/宽松双路径/
  投影/数值子集/纯文本回退/保序/异源类型归一化,纯函数直测。
- **writeback 治理单测**(tests/test_rag_writeback.py 或并入既有 rag 测试文件):
  幂等覆盖/保旧向量/上限淘汰/失败不阻断。
- **评估器本身**:由真实冒烟守护(016 收尾跑 smoke+full+multiturn 全量,
  记录首基线进 README 基线表)。

## Open Questions

- 评估问句自身的 query_history 沉淀是否该在评估前清场?当前裁定:保留
  (真实链路口径),README 记录清场命令供重复性要求高的场景(prompt 类
  改动评估);基线表注明环境口径。
- olist 上限题(订单数 top-N)固有退化(大量并列值 1,上游同款):gold
  tiebreaker 后 gen 无 tiebreaker 仍可能 fail,属可接受边界,README 声明。
- cap=500 的量级依据:上游无上限实测沉淀 <百行/租户源;500 是防膨胀闸门
  而非常态工作集,非调优点。
