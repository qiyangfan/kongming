# 016 eval+ai-eval 域:Execution Match 双分引擎与评估集

## Why

001–015 已交付完整问答链路(认证/RBAC/租户/防火墙/RAG/反馈/审计),但质量仍是
「冒烟肉眼判断」——prompt 或链路改动对生成质量的影响没有量化口径,回归无从拦截。
上游 38 变更验证过的评估体系(Execution Match 黄金指标 + gold 数据集 + 基线管理)
是本域的成熟参考;memory 已定调:**不引入学术评估集**(Spider/BIRD/DuSQL 评
「生成器」,与 Kongming 评「整条链路」错配),在已装载的 sakila/olist 示例数据集
上各写 15-20 题/租户 gold。

同时搭车清偿两笔挂账:009③ query_history 无去重/上限(每次成功裸 INSERT),
014 O4 反馈弹窗预填原 SQL 直接提交会重复沉淀——同题行无限膨胀会挤占 few-shot
召回位(≤3 条),治理时机与本域绑定(HANDOFF 既有裁定)。

## What Changes

- **评估器**(apps/api/evals/,pytest 形态,与 tests/ 目录平级、默认门禁隔离):
  登录真实服务(api 8000+agent 8001)→ 调 /chat/query 全链路 → gold_sql 直连
  PG 执行 → 严格/宽松双分匹配(列等价+投影行近似 / 数值子集双路径,gold 含
  ORDER BY 保序)→ smoke/full/multiturn 三层 marker。
- **评估集**:sakila 22 题(含 smoke×2)+ olist 18 题(含 smoke×2)(题型:single_table/filter/aggregate/
  join_2/join_3/join_4plus)+ 多轮 turns 用例(指代/省略/排序延续/过滤追加四类,
  sakila 6 case+olist 4 case)+ 动态数据源用例(sakila_dyn ×3+跨租户拒绝 ×1)。
- **Recall@K**:间接口径——生成 SQL 涉及表(sqlglot 抽表,复用 014 feedback 的
  CTE 排除实现)覆盖 expected_tables,输出命中率。
- **基线管理**:evals/README.md 基线表记录四组分数(严格/宽松/多轮/动态),
  变更须标注原因;宽松分劣化 ≥5pp 逐题溯源。
- **匹配器单测进默认门禁**(tests/test_eval_matchers.py):匹配器为纯函数,
  正确性由常规单测守护,不依赖评估环境。
- **query_history 治理(搭车)**:0007 迁移(tenant_id+datasource_key+question
  唯一索引,存量去重保最新)+ writeback 改幂等 UPSERT(同题覆盖保留最新成功
  SQL;embedding 失败保留旧向量)+ 每租户+数据源维度总量上限(超出删最旧)。
- spec 修订三处:eval(动态数据源拒绝码对齐 Kongming 同形口径+评估题结果集
  上限句)、ai-eval(CI smoke 现实化+Recall@K 间接口径精确化)、rag(回写
  requirement 增治理条款)。

## Capabilities

### New Capabilities

(无——eval/ai-eval 主 spec 为平移既有资产,本变更兑现并修订之)

### Modified Capabilities

- `eval`:评估集结构条款修订(跨租户动态源拒绝码=同形 DATASOURCE_INVALID 而非
  上游 TENANT_FORBIDDEN;补「gold 结果集 ≤1000 行」编写约束与评估运行形态条款)
- `ai-eval`:CI smoke 守护条款现实化(CI 无评估环境,smoke 为本地/预发手动+
  匹配器单测进常规门禁);Recall@K 精确化为间接口径(生成 SQL 表覆盖)
- `rag`:「成功 SQL 回写 query_history」增治理条款(同题幂等覆盖+总量上限)

## Impact

- **代码**:apps/api/evals/ 新目录(conftest/matchers/test_eval/五份 yaml/
  README);apps/api/tests/test_eval_matchers.py + test_rag_writeback.py;
  kongming/rag repository+models writeback 改造
  (service 仅注释同步);alembic 0007 迁移;pyproject(testpaths/markers/pythonpath/
  dev 依赖 pyyaml)。
- **schema**(ADR-004,走新迁移):0007 为 rag.query_history 加唯一索引
  (tenant_id, datasource_key, question),存量重复行去重保留 created_at 最新;
  列零新增。
- **契约**:零端点变化,零契约漂移(评估器是进程外工具,不进 OpenAPI)。
- **信任边界**(ADR-002):评估器放 api 侧(apps/api/evals/)——需直连 PG 执行
  gold 与登录凭证,零凭证纪律的 agent 面不可持有;评估经 HTTP 打完整链路,
  与真实用户同路径。
- **上游参考**(/Users/solvela/nl2sql-data-platform/nl2sql-agent/tests/eval/):
  test_eval.py 三层 marker 结构、宽松匹配双路径算法、README 编写规范与基线表
  形态——参考逻辑全部重写为 Python 3.13/asyncpg/httpx 形态,不复制代码;
  tianchi.yaml 不引入(数据未装载)。

## Non-goals

- **不引入学术评估集**(Spider/BIRD/DuSQL/tianchi)——评生成器 vs 评整条链路
  错配(memory 定调;BIRD 外部知识题型留 rag 域远期参考)。
- **不做 CI 全自动评估**——CI 无 agent 进程/LLM 凭证/示例数据集,虚构运行
  立约无意义;smoke 为本地/预发手动形态,CI 仅守护匹配器单测。
- **不做评估平台化**(评估结果入库、评估管理端点、/admin 页面)——基线以
  README 表格为载体;评估平台随运营诉求远期另议。
- **不做 LLM-as-judge 语义评分**——当前只做确定性 Execution Match+Recall@K;
  LLM 评分随 prompt 护栏域评估。
- **query_history 不做 TTL 与相似问句聚类**——上限+同题幂等已消重复沉淀主通路;
  相似(非相同)问句靠向量召回排序自然处理(上游同构取舍),TTL 随运营另议。
- **web 零改动**——评估无新页面;评估器不产生面向用户的 API。
- **不做评估器定时调度**(nightly 自动跑)——评估须真 LLM 产生费用,人为触发
  是刻意的成本闸门。
